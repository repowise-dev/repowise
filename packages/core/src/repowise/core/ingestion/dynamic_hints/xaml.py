"""Dynamic-hint extractor for XAML data-binding surfaces.

Why this exists
===============
XAML (WPF, WinUI 3, UWP, MAUI, Avalonia/Uno via ``.axaml``) reaches
into the C# code graph through three vectors the AST never sees:

1. ``xmlns:vm="using:Acme.ViewModels"`` (WinUI / UWP / MAUI) or
   ``xmlns:vm="clr-namespace:Acme.ViewModels"`` (WPF) — declares that
   types in a namespace are addressable from this XAML file by the
   ``vm:`` prefix.
2. ``x:DataType="vm:GeneralViewModel"`` (compiled bindings) and
   ``DataType="vm:..."`` / ``TargetType="vm:..."`` — names a concrete
   C# type whose members are the binding source.
3. ``DataContext`` literal types like
   ``DataContext="{x:Type vm:SettingsViewModel}"``.

Without these edges, every ViewModel and every settings-model class
read by the view layer surfaces as an orphan — there is no ``using``
directive on the C# side either, because the consumer is the XAML.

Design
======
Pure regex pass over ``*.xaml`` and ``*.axaml`` files (avoids pulling
in an XML parser and works on partial / malformed XAML during a
mid-edit index). The class-name → file map is borrowed from
``DotNetProjectIndex.type_map`` (built lazily on first access) so we
don't re-walk the repo. When the index isn't available (no .csproj
in the repo), we silently emit no edges — XAML without a backing
.NET project doesn't have a target to point at.

The extractor is generic across WPF / WinUI / MAUI / Avalonia / Uno —
no framework-specific code paths. Adding a new XAML dialect is a matter
of extending ``_XMLNS_RE`` to handle a new namespace prefix scheme.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"bin", "obj", ".vs", "node_modules", ".git", "packages"}
_XAML_EXTS = (".xaml", ".axaml")

# xmlns:prefix="clr-namespace:Foo.Bar"        — WPF
# xmlns:prefix="clr-namespace:Foo.Bar;assembly=Acme.UI" — WPF cross-assembly
# xmlns:prefix="using:Foo.Bar"                — WinUI / UWP / MAUI
# xmlns:prefix="https://github.com/avaloniaui" — Avalonia default (not a CLR map)
_XMLNS_RE = re.compile(
    r"""xmlns:(\w+)\s*=\s*["'](?:using:|clr-namespace:)([\w.]+)(?:;assembly=[^"']+)?["']""",
    re.IGNORECASE,
)

# x:DataType / DataType / TargetType / d:DataContext attribute values.
# Captures both `prefix:TypeName` and bare `TypeName` forms.
_TYPE_ATTR_RE = re.compile(
    r"""(?:x:DataType|d?:?DataType|TargetType|x:TypeArguments)\s*=\s*["']"""
    r"""(?:(\w+):)?(\w+)["']""",
    re.IGNORECASE,
)

# DataContext="{x:Type vm:Foo}" — the markup-extension form. Less
# common but used in WPF tooling.
_XTYPE_RE = re.compile(
    r"""\{\s*x:Type\s+(?:(\w+):)?(\w+)\s*\}""",
    re.IGNORECASE,
)


class XamlDynamicHints(DynamicHintExtractor):
    """Emit ``dynamic_uses`` edges from XAML files to the C# types they bind to."""

    name = "xaml"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        # Cheap pre-flight: any XAML in the tree at all?
        xaml_files = list(_iter_xaml_files(repo_root))
        if not xaml_files:
            return []

        # Reuse the existing C# type index — it already walks every .cs
        # file under every .csproj and dedupes builtins. Building it
        # here keeps XAML resolution cross-project / cross-repo
        # consistent with how `using` directives resolve.
        type_map = _load_type_map(repo_root)
        if not type_map:
            return []

        edges: list[DynamicEdge] = []
        repo_root_resolved = repo_root.resolve()

        for xaml_path in xaml_files:
            try:
                rel = xaml_path.resolve().relative_to(repo_root_resolved).as_posix()
            except ValueError:
                continue
            try:
                text = xaml_path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue

            prefix_to_namespace = _collect_prefix_namespaces(text)
            type_refs = _extract_type_references(text, prefix_to_namespace)

            for type_name in type_refs:
                targets = type_map.get(type_name)
                if not targets:
                    continue
                for target_abs in targets:
                    try:
                        target_rel = target_abs.resolve().relative_to(repo_root_resolved).as_posix()
                    except ValueError:
                        continue
                    if target_rel == rel:
                        continue
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=target_rel,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:binding",
                        )
                    )

        return edges


# ---------------------------------------------------------------------------
# Helpers (module-level so they're easy to unit-test in isolation)
# ---------------------------------------------------------------------------

def _iter_xaml_files(repo_root: Path):
    for ext in _XAML_EXTS:
        for path in repo_root.rglob(f"*{ext}"):
            try:
                rel = path.resolve().relative_to(repo_root.resolve())
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            yield path


def _collect_prefix_namespaces(text: str) -> dict[str, str]:
    """Return the ``xmlns:prefix → namespace`` map declared in *text*."""
    out: dict[str, str] = {}
    for match in _XMLNS_RE.finditer(text):
        prefix = match.group(1)
        namespace = match.group(2)
        out[prefix] = namespace
    return out


def _extract_type_references(text: str, prefix_to_ns: dict[str, str]) -> set[str]:
    """Return the set of unqualified type names referenced by *text*.

    Currently the namespace prefix is captured but not yet used to
    disambiguate — the resolver picks the first matching file by name.
    Reserved for a future refinement that prefers same-namespace
    candidates when the prefix maps to a known namespace.
    """
    names: set[str] = set()
    for match in _TYPE_ATTR_RE.finditer(text):
        type_name = match.group(2)
        if type_name and type_name[0].isupper():
            names.add(type_name)
    for match in _XTYPE_RE.finditer(text):
        type_name = match.group(2)
        if type_name and type_name[0].isupper():
            names.add(type_name)
    # The xmlns prefixes themselves never name a type, but their target
    # namespaces are a useful signal — left here as a hook for future
    # enhancement that resolves "every type in that namespace" when no
    # finer attribute is present.
    _ = prefix_to_ns
    return names


def _load_type_map(repo_root: Path) -> dict[str, list[Path]]:
    """Return the unqualified-type-name → defining-files map for *repo_root*.

    Wraps ``DotNetProjectIndex.build_index`` so XAML hints share the
    same authoritative type index as the resolver. If the project
    layout fails to parse (e.g. no .csproj at all), returns an empty
    map and the extractor early-exits.
    """
    try:
        # Local import to keep dynamic_hints package free of resolver
        # transitive imports at module load time.
        from ..resolvers.dotnet import build_index
    except ImportError:
        return {}
    try:
        index = build_index(repo_root)
    except Exception:  # pragma: no cover — defensive against partial repos
        return {}
    return index.type_map

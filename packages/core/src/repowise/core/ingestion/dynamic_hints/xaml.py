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

# ``<prefix:TypeName ...>`` element-tag references. WPF / WinUI / MAUI
# instantiate controls, converters and templates by writing them as
# XAML elements; the consumer code never says ``using`` for those
# types. We require the namespace prefix to avoid over-matching XAML's
# built-in elements (``<Grid>``, ``<TextBlock>``…), and to keep noise
# out we skip property-element syntax (``<Grid.Resources>``) by
# rejecting tags whose name contains a dot.
_ELEMENT_TAG_RE = re.compile(
    r"""<(\w+):([A-Z][\w]*)(?=[\s/>])""",
)

# <ResourceDictionary Source="..."/> — both standalone and inside a
# <ResourceDictionary.MergedDictionaries> block. Match attribute order
# agnostically; only the Source value is needed.
_RESOURCE_DICT_SOURCE_RE = re.compile(
    r"""<ResourceDictionary\b[^>]*?\bSource\s*=\s*["']([^"']+)["']""",
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
        # Even without a .NET project we can still resolve xaml→xaml
        # ResourceDictionary references, so don't early-exit on an empty
        # type_map — only the C# binding pass is gated on it.

        edges: list[DynamicEdge] = []
        repo_root_resolved = repo_root.resolve()

        # Build a {basename → [rel_paths]} index of every XAML in the
        # repo for absolute-source / pack-URI lookups. The relative-source
        # path resolves against the parent of the consuming XAML, so it
        # doesn't need the index.
        xaml_by_basename = _index_xaml_by_basename(xaml_files, repo_root_resolved)

        for xaml_path in xaml_files:
            try:
                rel = xaml_path.resolve().relative_to(repo_root_resolved).as_posix()
            except ValueError:
                continue
            try:
                text = xaml_path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue

            if type_map:
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

            # ResourceDictionary cross-references — pure xaml→xaml.
            for target_rel in _resolve_resource_dictionary_sources(
                text, rel, xaml_by_basename
            ):
                if target_rel == rel:
                    continue
                edges.append(
                    DynamicEdge(
                        source=rel,
                        target=target_rel,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:resource_dictionary",
                    )
                )

        return edges


# ---------------------------------------------------------------------------
# Helpers (module-level so they're easy to unit-test in isolation)
# ---------------------------------------------------------------------------

def _iter_xaml_files(repo_root: Path):
    from ._walk import iter_glob as _iter_glob
    for ext in _XAML_EXTS:
        for path in _iter_glob(repo_root, f"*{ext}"):
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
    # Element-tag references like ``<converters:BoolToVisibilityConverter ...>``.
    # The leading-uppercase guard is in the regex; we additionally skip
    # the ``ResourceDictionary`` tag because that's handled separately
    # and binding it to the C# type map would just produce noise.
    for match in _ELEMENT_TAG_RE.finditer(text):
        type_name = match.group(2)
        if type_name and type_name != "ResourceDictionary":
            names.add(type_name)
    # The xmlns prefixes themselves never name a type, but their target
    # namespaces are a useful signal — left here as a hook for future
    # enhancement that resolves "every type in that namespace" when no
    # finer attribute is present.
    _ = prefix_to_ns
    return names


def _index_xaml_by_basename(
    xaml_files: list[Path], repo_root_resolved: Path
) -> dict[str, list[str]]:
    """Return a ``{basename.lower(): [repo-relative paths]}`` index."""
    out: dict[str, list[str]] = {}
    for path in xaml_files:
        try:
            rel = path.resolve().relative_to(repo_root_resolved).as_posix()
        except ValueError:
            continue
        out.setdefault(path.name.lower(), []).append(rel)
    return out


# Strip leading pack URI / WinUI prefixes from a Source attribute value.
# Returns a path that's either repo-relative (leading "/") or
# source-file-relative. Returns ``None`` if the URI is opaque
# (HTTP/HTTPS or unrecognised scheme).
_PACK_URI_PREFIX_RE = re.compile(
    r"""^pack://application:,,,/(?:[^/]+;component/)?""", re.IGNORECASE
)
_MS_APPX_PREFIX_RE = re.compile(r"""^ms-appx:///?""", re.IGNORECASE)


def _normalise_source_uri(raw: str) -> str | None:
    """Reduce a XAML ``Source`` URI to a plain path.

    Recognised forms (case-insensitive):

      * ``pack://application:,,,/Assembly;component/Themes/Light.xaml`` → ``Themes/Light.xaml``
      * ``pack://application:,,,/Themes/Light.xaml`` → ``Themes/Light.xaml``
      * ``ms-appx:///Themes/Light.xaml`` → ``Themes/Light.xaml``
      * ``/Themes/Light.xaml`` → ``Themes/Light.xaml`` (treated as repo-rooted)
      * ``Themes/Light.xaml`` → ``Themes/Light.xaml`` (relative to caller)

    Returns ``None`` for HTTP-style absolute URIs.
    """
    raw = raw.strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower.startswith(("http://", "https://")):
        return None
    raw = _PACK_URI_PREFIX_RE.sub("", raw)
    raw = _MS_APPX_PREFIX_RE.sub("", raw)
    return raw.lstrip("/")


def _resolve_resource_dictionary_sources(
    text: str, source_rel: str, xaml_by_basename: dict[str, list[str]]
) -> list[str]:
    """Return the list of XAML files referenced from *source_rel*.

    Resolution order, per source:
      1. Treat as a path relative to the parent directory of *source_rel*.
         If a XAML file exists there, use it.
      2. Fall back to basename matching across the repo's XAML index.
         When multiple files share a basename we emit edges to all of
         them — over-emit beats under-emit for the dead-code use case.
    """
    if "<ResourceDictionary" not in text and "<ResourceDictionary" not in text.lower():
        return []
    results: list[str] = []
    seen: set[str] = set()
    parent_dir = source_rel.rsplit("/", 1)[0] if "/" in source_rel else ""
    for match in _RESOURCE_DICT_SOURCE_RE.finditer(text):
        raw = match.group(1)
        normalised = _normalise_source_uri(raw)
        if not normalised:
            continue
        candidate_rel = (
            f"{parent_dir}/{normalised}" if parent_dir and not raw.startswith("/") else normalised
        )
        candidate_rel = _normpath(candidate_rel)
        basename = candidate_rel.rsplit("/", 1)[-1].lower()
        # Repo-wide basename index — accept any match. When the relative
        # path happens to land on an actual file the index will contain
        # it; otherwise we still attach to same-named files elsewhere.
        for hit in xaml_by_basename.get(basename, ()):
            if hit not in seen:
                seen.add(hit)
                results.append(hit)
    return results


def _normpath(path: str) -> str:
    """Collapse ``..`` segments without touching the filesystem."""
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts:
            parts.pop()
            continue
        if part == "..":
            continue
        parts.append(part)
    return "/".join(parts)


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

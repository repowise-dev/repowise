"""Dynamic-hint extractor for .NET / C# patterns that escape static analysis.

The .NET ecosystem leans heavily on conventions that the AST never sees:

- ``services.AddScoped<IFoo, Foo>()`` — DI container registration that
  wires consumers (which depend on ``IFoo``) to producers (the ``Foo``
  implementation type) only at runtime.
- ``Activator.CreateInstance(typeof(T))`` / ``Type.GetType("X.Y.Z")`` —
  reflection-driven type loading.
- ``[assembly: InternalsVisibleTo("Other.Tests")]`` — cross-project
  visibility that pretends the friend assembly imports everything
  without writing any using directive.

These patterns produce ``DynamicEdge`` rows so the dead-code analyser
won't flag DI-registered types and the graph reflects the de facto
dependency surface.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"bin", "obj", ".vs", "node_modules", ".git", "packages"}

# services.AddScoped<IFoo, Foo>() / AddSingleton<...> / AddTransient<...>
_DI_GENERIC_RE = re.compile(
    r"\.\s*Add(?:Scoped|Singleton|Transient|HostedService)\s*<\s*([\w.]+)\s*(?:,\s*([\w.]+)\s*)?>"
)

# Activator.CreateInstance(typeof(Foo)) / Activator.CreateInstance("Acme.Foo")
_ACTIVATOR_TYPEOF_RE = re.compile(r"Activator\.CreateInstance\s*\(\s*typeof\s*\(\s*([\w.]+)\s*\)")
_ACTIVATOR_STRING_RE = re.compile(r"Activator\.CreateInstance\s*\(\s*[\"']([\w.]+)[\"']")

# Type.GetType("Acme.Foo")
_TYPE_GETTYPE_RE = re.compile(r"Type\.GetType\s*\(\s*[\"']([\w.]+)[\"']")

# [assembly: InternalsVisibleTo("Other.Tests")]
_INTERNALS_VISIBLE_RE = re.compile(
    r"\[\s*assembly\s*:\s*InternalsVisibleTo\s*\(\s*[\"']([^\"']+)[\"']"
)


class DotNetDynamicHints(DynamicHintExtractor):
    """Discover DI registrations, reflection, and assembly-level hints in .NET."""

    name = "dotnet"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        # Build a class-name → file index in one pass so the regex hits below
        # can resolve target file paths cheaply. We deliberately use a regex
        # rather than re-running the AST parser — this extractor runs after
        # parsing and operates on raw file text.
        type_to_file: dict[str, str] = {}
        cs_files: list[tuple[Path, str]] = []  # (path, text)
        for cs in repo_root.rglob("*.cs"):
            if any(part in _SKIP_DIRS for part in cs.parts):
                continue
            try:
                text = cs.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            try:
                rel = cs.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue
            cs_files.append((cs, text))
            # Only index publicly-declared top-level types — generic regex; a
            # collision falls back to the first match seen.
            for match in re.finditer(
                r"\b(?:class|interface|struct|record(?:\s+(?:class|struct))?|enum)\s+([A-Z]\w*)",
                text,
            ):
                name = match.group(1)
                type_to_file.setdefault(name, rel)

        def _short(name: str) -> str:
            return name.rsplit(".", 1)[-1]

        for cs, text in cs_files:
            try:
                rel = cs.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

            # ---- DI: AddScoped<IFoo, Foo>() ----
            for match in _DI_GENERIC_RE.finditer(text):
                first = _short(match.group(1))
                second = _short(match.group(2)) if match.group(2) else None
                # When two type args are present, edge: registration site → impl
                # When one type arg is present, edge: registration site → that type
                target_name = second or first
                target = type_to_file.get(target_name)
                if target and target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:di_register",
                        )
                    )
                # Also link interface → impl when both are present so the
                # interface file is recorded as having a real implementation
                # (helps dead-code analysis treat unused interfaces correctly).
                if second is not None:
                    iface_target = type_to_file.get(first)
                    impl_target = type_to_file.get(second)
                    if iface_target and impl_target and iface_target != impl_target:
                        edges.append(
                            DynamicEdge(
                                source=iface_target,
                                target=impl_target,
                                edge_type="dynamic_uses",
                                hint_source=f"{self.name}:di_interface_to_impl",
                            )
                        )

            # ---- Reflection: Activator.CreateInstance(typeof(...)) ----
            for match in _ACTIVATOR_TYPEOF_RE.finditer(text):
                target = type_to_file.get(_short(match.group(1)))
                if target and target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:activator",
                        )
                    )

            # ---- Reflection: Activator.CreateInstance("Acme.Foo") ----
            for match in _ACTIVATOR_STRING_RE.finditer(text):
                target = type_to_file.get(_short(match.group(1)))
                if target and target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:activator_string",
                        )
                    )

            # ---- Reflection: Type.GetType("Acme.Foo") ----
            for match in _TYPE_GETTYPE_RE.finditer(text):
                target = type_to_file.get(_short(match.group(1)))
                if target and target != rel:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:type_gettype",
                        )
                    )

            # ---- [assembly: InternalsVisibleTo("Other.Tests")] ----
            for match in _INTERNALS_VISIBLE_RE.finditer(text):
                friend = match.group(1)
                # Map by best-effort: AssemblyName usually equals the project's
                # csproj filename. We can't always resolve precisely without the
                # DotNetProjectIndex, so we record the friend as a synthetic
                # external target. The dead-code analyser uses InternalsVisibleTo
                # presence as a strong "type may be used" signal regardless of
                # whether we can resolve it.
                edges.append(
                    DynamicEdge(
                        source=rel,
                        target=f"external:friend:{friend}",
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:internals_visible_to",
                    )
                )

        return edges

"""Unit tests for the VB.NET import resolver and shared .NET project index.

VB.NET reuses the exact same ``DotNetProjectIndex``/``resolvers/dotnet``
machinery C# uses (docs/architecture/vbnet-support.md D4), extended for
two VB-specific semantics this module exercises directly:

1. ``.vbproj``'s ``<Import Include="X"/>`` ItemGroup entries (VB's
   project-level root-import, distinct from MSBuild's
   ``<Import Project="...">`` file-include directive).
2. ``RootNamespace`` prepended to *every* VB declaration, not just
   namespace-less files — scoped to VB only, C# is unaffected.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.dotnet.index import build_index
from repowise.core.ingestion.resolvers.dotnet.msbuild import parse_vbproj
from repowise.core.ingestion.resolvers.dotnet.namespace_map import (
    build_namespace_map,
    declared_vbnet_namespaces,
    scan_vbnet_type_declarations,
)
from repowise.core.ingestion.resolvers.dotnet.solution import parse_sln
from repowise.core.ingestion.resolvers.vbnet import resolve_vbnet_import


def _vbproj(root_namespace: str = "", deps: list[str] = (), root_imports: list[str] = ()) -> str:
    refs = "\n".join(f'    <ProjectReference Include="{p}" />' for p in deps)
    imports = "\n".join(f'    <Import Include="{ns}" />' for ns in root_imports)
    ns_elem = f"    <RootNamespace>{root_namespace}</RootNamespace>" if root_namespace else ""
    return f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
{ns_elem}
  </PropertyGroup>
  <ItemGroup>
{refs}
{imports}
  </ItemGroup>
</Project>
"""


def _ctx_for(repo: Path) -> ResolverContext:
    vb_files = list(repo.rglob("*.vb"))
    path_set: set[str] = set()
    stem_map: dict[str, list[str]] = {}
    for f in vb_files:
        rel = f.resolve().relative_to(repo.resolve()).as_posix()
        path_set.add(rel)
        stem_map.setdefault(f.stem.lower(), []).append(rel)
    return ResolverContext(path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo)


class TestVbprojParsing:
    def test_import_include_becomes_project_using(self, tmp_path: Path) -> None:
        vbproj = tmp_path / "Foo.vbproj"
        vbproj.write_text(_vbproj(root_imports=["System", "System.Linq"]))
        project = parse_vbproj(vbproj)
        assert project is not None
        assert project.project_usings == {"System", "System.Linq"}

    def test_import_project_directive_is_not_a_namespace(self, tmp_path: Path) -> None:
        """`<Import Project="...">` (MSBuild file-include) must never be
        mistaken for a namespace root-import — only `Include=` counts."""
        vbproj = tmp_path / "Foo.vbproj"
        vbproj.write_text(
            """<Project Sdk="Microsoft.NET.Sdk">
  <Import Project="Shared.props" />
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
"""
        )
        project = parse_vbproj(vbproj)
        assert project is not None
        assert project.project_usings == set()

    def test_root_namespace_parsed(self, tmp_path: Path) -> None:
        vbproj = tmp_path / "Foo.vbproj"
        vbproj.write_text(_vbproj(root_namespace="Acme.Foo"))
        project = parse_vbproj(vbproj)
        assert project is not None
        assert project.root_namespace == "Acme.Foo"

    def test_project_reference_extracted(self, tmp_path: Path) -> None:
        vbproj = tmp_path / "Foo.vbproj"
        vbproj.write_text(_vbproj(deps=["../Bar/Bar.vbproj"]))
        project = parse_vbproj(vbproj)
        assert project is not None
        assert len(project.project_references) == 1
        assert project.project_references[0].name == "Bar.vbproj"


class TestSlnAcceptsVbproj:
    def test_vbproj_entry_parsed(self, tmp_path: Path) -> None:
        sln = tmp_path / "App.sln"
        sln.write_text(
            'Project("{F184B08F-C81C-45F6-A57F-5ABD9991F28F}") = '
            '"App", "App.vbproj", "{11111111-1111-1111-1111-111111111111}"\n'
            "EndProject\n"
        )
        entries = parse_sln(sln)
        assert len(entries) == 1
        assert entries[0].csproj.name == "App.vbproj"


class TestVbnetNamespaceScanning:
    def test_declared_namespace_block(self) -> None:
        text = "Namespace Acme.Widgets\nEnd Namespace\n"
        assert declared_vbnet_namespaces(text) == ["Acme.Widgets"]

    def test_type_declarations_with_nesting(self) -> None:
        text = """\
Namespace Acme

    Public Class Outer

        Public Class Inner
        End Class

    End Class

End Namespace
"""
        decls = {d.name: d for d in scan_vbnet_type_declarations(text)}
        assert decls["Outer"].qualified == "Outer"
        assert decls["Inner"].qualified == "Outer.Inner"
        assert decls["Inner"].namespace == "Acme"


class TestRootNamespacePrepending:
    """D4: RootNamespace is prepended to every VB declaration, scoped to
    VB only — this is genuinely new logic, not an extension of C#'s
    namespace resolution (C# never prepends RootNamespace anywhere)."""

    def test_prepended_for_file_with_no_namespace_block(self, tmp_path: Path) -> None:
        vb_file = tmp_path / "Widget.vb"
        vb_file.write_text("Public Class Widget\nEnd Class\n")
        namespaces, types, _partials = build_namespace_map(
            [vb_file.resolve()],
            texts={vb_file.resolve(): vb_file.read_text()},
            vb_root_namespaces={vb_file.resolve(): "Acme.App"},
        )
        assert "Acme.App" in namespaces
        # type_map is namespace-agnostic — bare name only, unaffected by
        # RootNamespace (see build_namespace_map's docstring).
        assert "Widget" in types

    def test_prepended_and_concatenated_with_explicit_block(self, tmp_path: Path) -> None:
        vb_file = tmp_path / "Widget.vb"
        vb_file.write_text(
            "Namespace Widgets\n    Public Class Widget\n    End Class\nEnd Namespace\n"
        )
        namespaces, _types, _partials = build_namespace_map(
            [vb_file.resolve()],
            texts={vb_file.resolve(): vb_file.read_text()},
            vb_root_namespaces={vb_file.resolve(): "Acme.App"},
        )
        assert "Acme.App.Widgets" in namespaces
        assert "Widgets" not in namespaces  # bare declared name never used alone

    def test_no_root_namespace_leaves_declaration_bare(self, tmp_path: Path) -> None:
        vb_file = tmp_path / "Widget.vb"
        vb_file.write_text("Namespace Widgets\nEnd Namespace\n")
        namespaces, _types, _partials = build_namespace_map(
            [vb_file.resolve()],
            texts={vb_file.resolve(): vb_file.read_text()},
        )
        assert "Widgets" in namespaces

    def test_csharp_files_unaffected_by_vb_root_namespaces_param(self, tmp_path: Path) -> None:
        cs_file = tmp_path / "Widget.cs"
        cs_file.write_text("namespace Widgets { public class Widget {} }\n")
        namespaces, _types, _partials = build_namespace_map(
            [cs_file.resolve()],
            texts={cs_file.resolve(): cs_file.read_text()},
            vb_root_namespaces={cs_file.resolve(): "ShouldNeverApply"},
        )
        assert "Widgets" in namespaces
        assert "ShouldNeverApply.Widgets" not in namespaces


class TestResolveVbnetImport:
    def test_resolves_within_same_project(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "App"
        proj_dir.mkdir()
        (proj_dir / "App.vbproj").write_text(_vbproj(root_namespace="Acme.App"))
        # An explicit block gives Widget.vb a namespace distinct from bare
        # RootNamespace-only files (Program.vb below), so the import target
        # is unambiguous.
        (proj_dir / "Widget.vb").write_text(
            "Namespace Widgets\n    Public Class Widget\n    End Class\nEnd Namespace\n"
        )
        (proj_dir / "Program.vb").write_text("Imports Acme.App.Widgets\n")

        ctx = _ctx_for(tmp_path)
        resolved = resolve_vbnet_import("Acme.App.Widgets", "App/Program.vb", ctx)
        assert resolved == "App/Widget.vb"

    def test_prefers_referenced_project_over_unrelated(self, tmp_path: Path) -> None:
        domain_dir = tmp_path / "Domain"
        domain_dir.mkdir()
        (domain_dir / "Domain.vbproj").write_text(_vbproj(root_namespace="Acme.Domain"))
        (domain_dir / "User.vb").write_text("Public Class User\nEnd Class\n")

        api_dir = tmp_path / "Api"
        api_dir.mkdir()
        (api_dir / "Api.vbproj").write_text(
            _vbproj(root_namespace="Acme.Api", deps=["../Domain/Domain.vbproj"])
        )
        (api_dir / "Program.vb").write_text("Imports Acme.Domain\n")

        ctx = _ctx_for(tmp_path)
        resolved = resolve_vbnet_import("Acme.Domain", "Api/Program.vb", ctx)
        assert resolved == "Domain/User.vb"

    def test_build_index_discovers_vbproj_and_vb_files(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "App"
        proj_dir.mkdir()
        (proj_dir / "App.vbproj").write_text(_vbproj(root_namespace="Acme.App"))
        (proj_dir / "Widget.vb").write_text("Public Class Widget\nEnd Class\n")

        index = build_index(tmp_path)
        assert len(index.projects) == 1
        proj = next(iter(index.projects.values()))
        assert proj.root_namespace == "Acme.App"
        assert "Acme.App" in index.namespace_map

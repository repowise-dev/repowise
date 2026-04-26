"""Unit tests for the C# / .NET-aware import resolver.

The resolver is project-graph aware: it parses ``.csproj`` and ``.sln``
files under the repo, builds a namespace → file map, and ranks
candidates by enclosing project + ProjectReference relationships. These
tests build small repos on disk (via ``tmp_path``) so the index parses
real MSBuild XML.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.csharp import resolve_csharp_import
from repowise.core.ingestion.resolvers.dotnet import (
    DotNetProjectIndex,
    build_namespace_map,
    parse_csproj,
    parse_sln,
)
from repowise.core.ingestion.resolvers.dotnet.global_usings import scan_global_usings
from repowise.core.ingestion.resolvers.dotnet.index import build_index
from repowise.core.ingestion.resolvers.dotnet.namespace_map import declared_namespaces


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csproj(deps: list[str] = (), packages: list[tuple[str, str]] = (), implicit: bool = True) -> str:
    refs = "\n".join(f'    <ProjectReference Include="{p}" />' for p in deps)
    pkgs = "\n".join(
        f'    <PackageReference Include="{name}" Version="{ver}" />' for name, ver in packages
    )
    return f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>{"enable" if implicit else "disable"}</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
{refs}
{pkgs}
  </ItemGroup>
</Project>
"""


def _ctx_for(repo: Path) -> ResolverContext:
    """Build a ResolverContext rooted at *repo* with all .cs files indexed."""
    cs_files = [p for p in repo.rglob("*.cs")]
    path_set: set[str] = set()
    stem_map: dict[str, list[str]] = {}
    for cs in cs_files:
        rel = cs.resolve().relative_to(repo.resolve()).as_posix()
        path_set.add(rel)
        stem = cs.stem.lower()
        stem_map.setdefault(stem, []).append(rel)
    return ResolverContext(
        path_set=path_set,
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo,
    )


# ---------------------------------------------------------------------------
# .csproj parsing
# ---------------------------------------------------------------------------


class TestCsprojParsing:
    def test_extracts_project_and_package_references(self, tmp_path: Path) -> None:
        csproj_path = tmp_path / "Foo.csproj"
        csproj_path.write_text(
            _csproj(
                deps=[r"..\Bar\Bar.csproj"],
                packages=[("Newtonsoft.Json", "13.0.1"), ("Serilog", "3.0.0")],
            )
        )
        proj = parse_csproj(csproj_path)
        assert proj is not None
        assert proj.implicit_usings is True
        assert proj.package_references == {"Newtonsoft.Json", "Serilog"}
        # ProjectReference Include used Windows-style backslashes — the
        # parser must normalise to forward slashes and resolve.
        assert any(p.name == "Bar.csproj" for p in proj.project_references)

    def test_handles_legacy_xml_namespace(self, tmp_path: Path) -> None:
        csproj_path = tmp_path / "Legacy.csproj"
        csproj_path.write_text(
            """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="14.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <RootNamespace>MyOrg.Legacy</RootNamespace>
    <AssemblyName>MyOrg.Legacy</AssemblyName>
  </PropertyGroup>
</Project>
"""
        )
        proj = parse_csproj(csproj_path)
        assert proj is not None
        assert proj.root_namespace == "MyOrg.Legacy"
        assert proj.assembly_name == "MyOrg.Legacy"

    def test_returns_none_on_invalid_xml(self, tmp_path: Path) -> None:
        bad = tmp_path / "Bad.csproj"
        bad.write_text("not <xml at all >")
        assert parse_csproj(bad) is None


# ---------------------------------------------------------------------------
# .sln parsing
# ---------------------------------------------------------------------------


class TestSlnParsing:
    def test_extracts_csproj_entries(self, tmp_path: Path) -> None:
        # Create the .csproj files referenced by the .sln so paths resolve.
        (tmp_path / "Api").mkdir()
        (tmp_path / "Api" / "Api.csproj").write_text(_csproj())
        (tmp_path / "Domain").mkdir()
        (tmp_path / "Domain" / "Domain.csproj").write_text(_csproj())

        sln = tmp_path / "MySln.sln"
        sln.write_text(
            """Microsoft Visual Studio Solution File, Format Version 12.00
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Api", "Api\\Api.csproj", "{AAAAAAAA-1111-2222-3333-444444444444}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Domain", "Domain\\Domain.csproj", "{BBBBBBBB-1111-2222-3333-444444444444}"
EndProject
Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "Solution Items", "Solution Items", "{CCCCCCCC-1111-2222-3333-444444444444}"
EndProject
"""
        )
        entries = parse_sln(sln)
        names = {e.name for e in entries}
        # Solution folder must be skipped.
        assert names == {"Api", "Domain"}


# ---------------------------------------------------------------------------
# Namespace map + global usings
# ---------------------------------------------------------------------------


class TestNamespaceMap:
    def test_block_form(self) -> None:
        text = "namespace Foo.Bar { public class A {} }"
        assert declared_namespaces(text) == ["Foo.Bar"]

    def test_file_scoped(self) -> None:
        text = "namespace Foo.Bar.Baz;\npublic class A {}"
        assert declared_namespaces(text) == ["Foo.Bar.Baz"]

    def test_multiple_namespaces_in_one_file(self) -> None:
        text = "namespace A { class X {} }\nnamespace B { class Y {} }"
        assert declared_namespaces(text) == ["A", "B"]

    def test_build_namespace_map(self, tmp_path: Path) -> None:
        (tmp_path / "user.cs").write_text("namespace Domain.Users; class User {}")
        (tmp_path / "order.cs").write_text("namespace Domain.Orders; class Order {}")
        m = build_namespace_map([tmp_path / "user.cs", tmp_path / "order.cs"])
        assert "Domain.Users" in m
        assert "Domain.Orders" in m


class TestGlobalUsings:
    def test_scan_plain_global_using(self) -> None:
        text = "global using System;\nglobal using System.Linq;"
        assert scan_global_usings(text) == ["System", "System.Linq"]

    def test_scan_global_using_static(self) -> None:
        text = "global using static System.Math;"
        assert scan_global_usings(text) == ["System.Math"]

    def test_scan_global_using_alias(self) -> None:
        text = "global using MyAlias = Some.Long.Namespace;"
        assert scan_global_usings(text) == ["Some.Long.Namespace"]


# ---------------------------------------------------------------------------
# End-to-end resolver
# ---------------------------------------------------------------------------


class TestResolverEndToEnd:
    def _make_solution(self, repo: Path) -> None:
        """Create a 2-project solution: Api references Domain."""
        (repo / "src" / "Api").mkdir(parents=True)
        (repo / "src" / "Domain").mkdir(parents=True)

        (repo / "src" / "Api" / "Api.csproj").write_text(
            _csproj(
                deps=[r"..\Domain\Domain.csproj"],
                packages=[("Newtonsoft.Json", "13.0.1"), ("Microsoft.AspNetCore.App", "8.0.0")],
            )
        )
        (repo / "src" / "Domain" / "Domain.csproj").write_text(_csproj())

        (repo / "src" / "Domain" / "User.cs").write_text(
            "namespace Acme.Domain;\npublic record User(string Name);\n"
        )
        (repo / "src" / "Api" / "UsersController.cs").write_text(
            "using Acme.Domain;\nnamespace Acme.Api;\npublic class UsersController {}\n"
        )

    def test_resolves_cross_project_via_namespace(self, tmp_path: Path) -> None:
        self._make_solution(tmp_path)
        ctx = _ctx_for(tmp_path)
        importer = "src/Api/UsersController.cs"
        result = resolve_csharp_import("Acme.Domain", importer, ctx)
        assert result == "src/Domain/User.cs"

    def test_external_nuget_package(self, tmp_path: Path) -> None:
        self._make_solution(tmp_path)
        ctx = _ctx_for(tmp_path)
        importer = "src/Api/UsersController.cs"
        # Newtonsoft.Json is declared as a PackageReference on Api; the same
        # namespace as the package id resolves to a nuget: external node.
        result = resolve_csharp_import("Newtonsoft.Json", importer, ctx)
        assert result is not None and result.startswith("external:nuget:")

    def test_unknown_namespace_falls_to_external(self, tmp_path: Path) -> None:
        self._make_solution(tmp_path)
        ctx = _ctx_for(tmp_path)
        result = resolve_csharp_import("Totally.Unknown.Thing", "src/Api/UsersController.cs", ctx)
        assert result is not None and result.startswith("external:")

    def test_no_repo_path_falls_back_to_stem_match(self, tmp_path: Path) -> None:
        # Repo without .csproj — exercise the legacy path.
        (tmp_path / "loose.cs").write_text("namespace Loose; class L {}")
        path_set = {"loose.cs"}
        stem_map = {"loose": ["loose.cs"]}
        ctx = ResolverContext(
            path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=None
        )
        assert resolve_csharp_import("Loose", "other.cs", ctx) == "loose.cs"


class TestProjectIndexCaching:
    def test_index_cached_on_context(self, tmp_path: Path) -> None:
        (tmp_path / "Api").mkdir()
        (tmp_path / "Api" / "Api.csproj").write_text(_csproj())
        (tmp_path / "Api" / "Foo.cs").write_text("namespace Foo;\nclass F {}")

        ctx = _ctx_for(tmp_path)
        from repowise.core.ingestion.resolvers.dotnet.index import get_or_build_index

        first = get_or_build_index(ctx)
        second = get_or_build_index(ctx)
        assert first is second  # cached on ctx
        assert isinstance(first, DotNetProjectIndex)
        assert first.repo_path == tmp_path.resolve()
        assert "Foo" in first.namespace_map


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("namespace Foo;", ["Foo"]),
        ("  namespace Foo.Bar.Baz {", ["Foo.Bar.Baz"]),
        ("namespace Foo_Bar.Baz123 ;", ["Foo_Bar.Baz123"]),
        ("// namespace NotReal;", []),
    ],
)
def test_namespace_regex_edge_cases(raw: str, expected: list[str]) -> None:
    assert declared_namespaces(raw) == expected

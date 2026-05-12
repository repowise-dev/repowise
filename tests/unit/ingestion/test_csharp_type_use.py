"""Unit tests for C# type-use edge emission.

Covers the chain from `@param.type` capture in the tree-sitter query
through ``TypeReference`` extraction, ``DotNetProjectIndex.type_map``
construction, candidate ranking by project enclosure, and graph-builder
edge emission. End-to-end cases mirror real DI-heavy layouts: a
controller takes an interface in its constructor; the interface lives
in a referenced project.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.models import FileInfo, ParsedFile, TypeReference
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.dotnet.index import build_index
from repowise.core.ingestion.resolvers.dotnet.namespace_map import (
    build_namespace_map,
    declared_type_names,
)
from repowise.core.ingestion.type_ref_resolution import resolve_type_refs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_CSPROJ_TEMPLATE = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
{refs}
  </ItemGroup>
</Project>
"""


def _csproj(refs: list[str] | None = None) -> str:
    refs = refs or []
    block = "\n".join(f'    <ProjectReference Include="{p}" />' for p in refs)
    return _CSPROJ_TEMPLATE.format(refs=block)


def _file_info(repo: Path, rel: str) -> FileInfo:
    """Build a minimal FileInfo for a .cs file at *rel*."""
    abs_path = repo / rel
    from datetime import UTC, datetime

    return FileInfo(
        path=rel,
        abs_path=str(abs_path.resolve()),
        language="csharp",
        size_bytes=abs_path.stat().st_size if abs_path.exists() else 0,
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


# ---------------------------------------------------------------------------
# Type-name regex
# ---------------------------------------------------------------------------


class TestDeclaredTypeNames:
    def test_class(self) -> None:
        assert declared_type_names("public class Foo {}") == ["Foo"]

    def test_interface(self) -> None:
        assert declared_type_names("internal interface IFoo {}") == ["IFoo"]

    def test_struct(self) -> None:
        assert declared_type_names("public readonly struct Point {}") == ["Point"]

    def test_record(self) -> None:
        assert declared_type_names("public record User(string Name);") == ["User"]

    def test_record_class(self) -> None:
        assert declared_type_names("public sealed record class User(string N);") == ["User"]

    def test_generic_strips_args(self) -> None:
        assert declared_type_names("public interface IRepo<T> where T : new() {}") == ["IRepo"]

    def test_partial_repeats(self) -> None:
        text = "partial class Foo {}\npartial class Foo {}"
        # Same name yields two matches; the type_map deduplicates per
        # file but preserves cross-file occurrences.
        assert declared_type_names(text) == ["Foo", "Foo"]

    def test_modifier_soup(self) -> None:
        text = "public partial sealed unsafe class Net {}"
        assert declared_type_names(text) == ["Net"]

    def test_comment_not_matched(self) -> None:
        # Leading whitespace + a comment shouldn't be matched. The
        # regex anchors on start-of-line+whitespace+modifier, so a
        # line starting with `//` falls through.
        assert declared_type_names("// class Foo {}") == []


# ---------------------------------------------------------------------------
# DotNetProjectIndex.type_map
# ---------------------------------------------------------------------------


class TestTypeMap:
    def test_built_from_repo(self, tmp_path: Path) -> None:
        (tmp_path / "Domain").mkdir()
        (tmp_path / "Domain" / "Domain.csproj").write_text(_csproj())
        (tmp_path / "Domain" / "User.cs").write_text(
            "namespace Acme.Domain;\npublic class User {}\n"
        )
        (tmp_path / "Domain" / "IRepo.cs").write_text(
            "namespace Acme.Domain;\npublic interface IRepo {}\n"
        )

        index = build_index(tmp_path)
        assert "User" in index.type_map
        assert "IRepo" in index.type_map
        assert len(index.type_map["User"]) == 1

    def test_rank_prefers_same_project_then_referenced(self, tmp_path: Path) -> None:
        # Three-project layout where the same type name exists in two
        # places: the consumer's own project (should rank first) and
        # a referenced project (second).
        (tmp_path / "A").mkdir()
        (tmp_path / "B").mkdir()
        (tmp_path / "C").mkdir()
        (tmp_path / "A" / "A.csproj").write_text(_csproj(refs=[r"..\B\B.csproj"]))
        (tmp_path / "B" / "B.csproj").write_text(_csproj())
        (tmp_path / "C" / "C.csproj").write_text(_csproj())
        (tmp_path / "A" / "Local.cs").write_text("namespace A;\nclass Helper {}\n")
        (tmp_path / "B" / "Ref.cs").write_text("namespace B;\nclass Helper {}\n")
        (tmp_path / "C" / "Other.cs").write_text("namespace C;\nclass Helper {}\n")

        index = build_index(tmp_path)
        a_file = (tmp_path / "A" / "Local.cs").resolve()
        ranked = index.rank_type_candidates("Helper", a_file)
        # First should be from referenced project B (A_file itself is
        # excluded from candidates as it's the source file); C should
        # come last because A doesn't reference it.
        paths = [p.resolve() for p in ranked]
        b_file = (tmp_path / "B" / "Ref.cs").resolve()
        c_file = (tmp_path / "C" / "Other.cs").resolve()
        assert paths.index(b_file) < paths.index(c_file)


# ---------------------------------------------------------------------------
# Parser surfaces TypeReference records on real C# source
# ---------------------------------------------------------------------------


class TestParserEmitsTypeRefs:
    def test_constructor_param(self, tmp_path: Path) -> None:
        rel = "Foo.cs"
        (tmp_path / rel).write_text(
            "namespace Demo;\n"
            "public class Foo {\n"
            "  public Foo(IBasketService basket) {}\n"
            "}\n"
        )
        parsed = ASTParser().parse_file(_file_info(tmp_path, rel), (tmp_path / rel).read_bytes())
        names = {r.type_name for r in parsed.type_refs}
        assert "IBasketService" in names
        # Origin should be classified as ctor_param
        refs_for = [r for r in parsed.type_refs if r.type_name == "IBasketService"]
        assert refs_for[0].origin == "ctor_param"

    def test_method_param(self, tmp_path: Path) -> None:
        rel = "Svc.cs"
        (tmp_path / rel).write_text(
            "namespace Demo;\n"
            "class Svc {\n"
            "  public void Handle(EventEnvelope env) {}\n"
            "}\n"
        )
        parsed = ASTParser().parse_file(_file_info(tmp_path, rel), (tmp_path / rel).read_bytes())
        refs = [r for r in parsed.type_refs if r.type_name == "EventEnvelope"]
        assert refs and refs[0].origin == "method_param"

    def test_record_primary_constructor(self, tmp_path: Path) -> None:
        rel = "Rec.cs"
        (tmp_path / rel).write_text(
            "namespace Demo;\n"
            "public record Rec(Address Address);\n"
        )
        parsed = ASTParser().parse_file(_file_info(tmp_path, rel), (tmp_path / rel).read_bytes())
        refs = [r for r in parsed.type_refs if r.type_name == "Address"]
        assert refs and refs[0].origin == "ctor_param"

    def test_builtin_types_skipped(self, tmp_path: Path) -> None:
        rel = "X.cs"
        (tmp_path / rel).write_text(
            "namespace Demo;\nclass X { public X(int n, string s, bool b) {} }\n"
        )
        parsed = ASTParser().parse_file(_file_info(tmp_path, rel), (tmp_path / rel).read_bytes())
        names = {r.type_name for r in parsed.type_refs}
        # None of int / string / bool / Task etc. should appear.
        assert names.isdisjoint({"int", "string", "bool"})

    def test_generic_uses_head_identifier(self, tmp_path: Path) -> None:
        rel = "G.cs"
        (tmp_path / rel).write_text(
            "namespace Demo;\nclass G { public G(IRepo<Basket> r) {} }\n"
        )
        parsed = ASTParser().parse_file(_file_info(tmp_path, rel), (tmp_path / rel).read_bytes())
        names = {r.type_name for r in parsed.type_refs}
        assert "IRepo" in names


# ---------------------------------------------------------------------------
# End-to-end: graph builder emits type_use edges (cross-project workspace)
# ---------------------------------------------------------------------------


class TestGraphEdgeEmission:
    def test_cross_project_workspace_edge(self, tmp_path: Path) -> None:
        # Two-project workspace: Api → Domain via ProjectReference.
        # Api/UserController.cs takes an IUserRepo (declared in Domain)
        # only as a ctor parameter, never via a using directive. The
        # type_use pass must connect the two files.
        (tmp_path / "src" / "Api").mkdir(parents=True)
        (tmp_path / "src" / "Domain").mkdir(parents=True)
        (tmp_path / "src" / "Api" / "Api.csproj").write_text(
            _csproj(refs=[r"..\Domain\Domain.csproj"])
        )
        (tmp_path / "src" / "Domain" / "Domain.csproj").write_text(_csproj())
        (tmp_path / "src" / "Domain" / "IUserRepo.cs").write_text(
            "namespace Acme.Domain;\npublic interface IUserRepo {}\n"
        )
        (tmp_path / "src" / "Api" / "UserController.cs").write_text(
            "namespace Acme.Api;\n"
            "public class UserController {\n"
            "  public UserController(IUserRepo repo) {}\n"
            "}\n"
        )

        parser = ASTParser()
        parsed_files: dict[str, ParsedFile] = {}
        for cs in tmp_path.rglob("*.cs"):
            rel = cs.resolve().relative_to(tmp_path.resolve()).as_posix()
            parsed = parser.parse_file(_file_info(tmp_path, rel), cs.read_bytes())
            parsed_files[rel] = parsed

        graph = nx.DiGraph()
        for rel in parsed_files:
            graph.add_node(rel, node_type="file", language="csharp")
        ctx = ResolverContext(
            path_set=set(parsed_files),
            stem_map={},
            graph=graph,
            repo_path=tmp_path,
        )

        emitted = resolve_type_refs(parsed_files, ctx, graph)
        assert emitted.get("csharp", 0) >= 1
        src = "src/Api/UserController.cs"
        dst = "src/Domain/IUserRepo.cs"
        assert graph.has_edge(src, dst)
        data = graph[src][dst]
        assert data["edge_type"] == "imports"
        assert data["via"] == "type_use"
        assert "IUserRepo" in data["type_uses"]


# ---------------------------------------------------------------------------
# Type-ref resolution is a no-op for non-C# languages
# ---------------------------------------------------------------------------


def test_resolve_type_refs_no_csharp(tmp_path: Path) -> None:
    # A Python parsed file with manually-attached type_refs (which the
    # parser never emits for Python today). The dispatcher must skip
    # languages without a registered strategy.
    from datetime import UTC, datetime

    info = FileInfo(
        path="a.py",
        abs_path=str((tmp_path / "a.py").resolve()),
        language="python",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = ParsedFile(
        file_info=info,
        symbols=[],
        imports=[],
        exports=[],
        type_refs=[TypeReference(type_name="Foo", line=1, origin="ctor_param")],
    )
    graph = nx.DiGraph()
    graph.add_node("a.py", node_type="file", language="python")
    ctx = ResolverContext(
        path_set={"a.py"}, stem_map={}, graph=graph, repo_path=tmp_path,
    )
    emitted = resolve_type_refs({"a.py": parsed}, ctx, graph)
    assert emitted == {}

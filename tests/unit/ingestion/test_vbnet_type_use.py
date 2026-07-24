"""Unit tests for VB.NET type-use edge emission (DI-edge resolution).

Covers the chain from `@param.type` capture in queries/vbnet.scm through
``TypeReference`` extraction, the shared ``DotNetProjectIndex.type_map``,
candidate ranking by project enclosure, and graph-builder edge emission —
reusing the exact same ``type_ref_resolution.py`` strategy C# uses (see
``_resolve_dotnet_type_refs``), only the query capture and
``TYPE_HEAD_EXTRACTORS`` entry differ per language.

Also documents the one known grammar gap this feature inherits: a
parameter *following* a generic `(Of ...)`-typed parameter in the same
parameter list is silently dropped, not misresolved (see
queries/vbnet.scm's `@param.type` section and docs/architecture/
vbnet-support.md's risk register).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.type_ref_resolution import resolve_type_refs

_VBPROJ_TEMPLATE = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
{refs}
  </ItemGroup>
</Project>
"""


def _vbproj(refs: list[str] | None = None) -> str:
    refs = refs or []
    block = "\n".join(f'    <ProjectReference Include="{p}" />' for p in refs)
    return _VBPROJ_TEMPLATE.format(refs=block)


def _file_info(repo: Path, rel: str) -> FileInfo:
    abs_path = repo / rel
    return FileInfo(
        path=rel,
        abs_path=str(abs_path.resolve()),
        language="vbnet",
        size_bytes=abs_path.stat().st_size if abs_path.exists() else 0,
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


class TestParserEmitsTypeRefs:
    def test_ctor_param_type_captured(self) -> None:
        src = b"""\
Public Class Widget
    Public Sub New(repo As IWidgetRepository)
    End Sub
End Class
"""
        fi = _file_info(Path("."), "Widget.vb")
        parsed = ASTParser().parse_file(fi, src)
        names = {t.type_name for t in parsed.type_refs}
        assert "IWidgetRepository" in names
        assert next(t.origin for t in parsed.type_refs if t.type_name == "IWidgetRepository") == (
            "ctor_param"
        )

    def test_dotted_type_resolves_to_rightmost_segment(self) -> None:
        src = b"""\
Public Class Widget
    Public Sub New(logger As Acme.Logging.ILogger)
    End Sub
End Class
"""
        fi = _file_info(Path("."), "Widget.vb")
        parsed = ASTParser().parse_file(fi, src)
        names = {t.type_name for t in parsed.type_refs}
        assert "ILogger" in names
        assert "Acme" not in names

    def test_builtin_primitive_type_filtered(self) -> None:
        src = b"""\
Public Class Widget
    Public Sub New(count As Integer)
    End Sub
End Class
"""
        fi = _file_info(Path("."), "Widget.vb")
        parsed = ASTParser().parse_file(fi, src)
        assert parsed.type_refs == []

    def test_generic_param_head_captured(self) -> None:
        """`List(Of Widget)` alone (or last in the list) captures its head
        type `List` fine — only a *trailing* sibling parameter is affected
        (see TestGenericParameterGap below)."""
        src = b"""\
Public Class Repo
    Public Sub New(items As List(Of Widget))
    End Sub
End Class
"""
        fi = _file_info(Path("."), "Repo.vb")
        parsed = ASTParser().parse_file(fi, src)
        names = {t.type_name for t in parsed.type_refs}
        assert "List" in names


class TestGenericParameterGap:
    """Documents the known grammar limitation: tree-sitter-vbnet 0.1.0's
    ERROR recovery around `(Of ...)` drops whatever parameter follows a
    generic-typed one — silently missing, never misattributed as a wrong
    type_use edge (see queries/vbnet.scm)."""

    def test_parameter_after_generic_is_not_captured(self) -> None:
        src = b"""\
Public Class Repo
    Public Sub New(items As List(Of Widget), logger As ILogger)
    End Sub
End Class
"""
        fi = _file_info(Path("."), "Repo.vb")
        parsed = ASTParser().parse_file(fi, src)
        names = {t.type_name for t in parsed.type_refs}
        assert "List" in names
        # The gap: ILogger is silently missing, not present with a wrong
        # target — this assertion pins the documented limitation so a
        # grammar upgrade that fixes it is a visible test change, not a
        # silent behavior shift.
        assert "ILogger" not in names


class TestGraphEdgeEmission:
    def test_cross_project_workspace_edge(self, tmp_path: Path) -> None:
        # Two-project workspace: Api -> Domain via ProjectReference.
        # Api/UserController.vb takes an IUserRepo (declared in Domain)
        # only as a ctor parameter, never via an Imports statement. The
        # type_use pass must connect the two files.
        (tmp_path / "src" / "Api").mkdir(parents=True)
        (tmp_path / "src" / "Domain").mkdir(parents=True)
        (tmp_path / "src" / "Api" / "Api.vbproj").write_text(
            _vbproj(refs=[r"..\Domain\Domain.vbproj"])
        )
        (tmp_path / "src" / "Domain" / "Domain.vbproj").write_text(_vbproj())
        (tmp_path / "src" / "Domain" / "IUserRepo.vb").write_text(
            "Namespace Acme.Domain\n    Public Interface IUserRepo\n    End Interface\nEnd Namespace\n"
        )
        (tmp_path / "src" / "Api" / "UserController.vb").write_text(
            "Namespace Acme.Api\n"
            "    Public Class UserController\n"
            "        Public Sub New(repo As IUserRepo)\n"
            "        End Sub\n"
            "    End Class\n"
            "End Namespace\n"
        )

        parser = ASTParser()
        parsed_files: dict[str, ParsedFile] = {}
        for vb in tmp_path.rglob("*.vb"):
            rel = vb.resolve().relative_to(tmp_path.resolve()).as_posix()
            parsed = parser.parse_file(_file_info(tmp_path, rel), vb.read_bytes())
            parsed_files[rel] = parsed

        graph = nx.DiGraph()
        for rel in parsed_files:
            graph.add_node(rel, node_type="file", language="vbnet")
        ctx = ResolverContext(
            path_set=set(parsed_files),
            stem_map={},
            graph=graph,
            repo_path=tmp_path,
        )

        emitted = resolve_type_refs(parsed_files, ctx, graph)
        assert emitted.get("vbnet", 0) >= 1
        src = "src/Api/UserController.vb"
        dst = "src/Domain/IUserRepo.vb"
        assert graph.has_edge(src, dst)
        data = graph[src][dst]
        assert data["edge_type"] == "type_use"
        assert "IUserRepo" in data["type_uses"]
        assert "IUserRepo" in data["imported_names"]

"""A file split into new files: the deleted source joins the base side."""

from __future__ import annotations

from types import MappingProxyType

from repowise.core.analysis.change_health.matcher import FindingMatcher
from repowise.core.analysis.change_health.moves import MoveMap
from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest
from repowise.core.analysis.health import HealthFindingData, Severity

from .conftest import python_complex


def compare(repo):
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    return service.compare(DeltaRequest(str(repo.path), "HEAD"))


def split(make_repo, *, extra: str = ""):
    """``tool.py`` split three ways into a package, so no part reads as a rename."""
    repo = make_repo()
    parts = [python_complex(name, 14 + i) for i, name in enumerate(("tangle", "knot", "braid"))]
    repo.commit("seed", {"tool.py": "\n\n".join(parts), "keep.py": "x = 1\n"})
    repo.remove("tool.py")
    repo.commit(
        "split",
        {
            "tool/__init__.py": "from .a import tangle\nfrom .b import knot\n",
            "tool/a.py": parts[0],
            "tool/b.py": parts[1] + extra,
            "tool/c.py": parts[2],
        },
    )
    return repo


def test_a_pure_split_introduces_nothing_and_names_where_the_code_went(make_repo):
    delta = compare(split(make_repo))

    assert delta.introduced_total == 0
    assert delta.resolved_total == 0
    assert delta.moved_total >= 3
    assert delta.moved_to == {"tool.py": ["tool/a.py", "tool/b.py", "tool/c.py"]}
    assert "tool.py" not in delta.skipped
    assert delta.status == "available"


def test_new_code_beside_a_split_is_still_introduced(make_repo):
    delta = compare(split(make_repo, extra="\n\n" + python_complex("fresh", 14)))

    introduced = [f for f in delta.findings if f.change_kind == "introduced"]
    assert {f.symbol for f in introduced} == {"fresh"}


def test_the_mcp_block_lists_the_deleted_source_and_its_destinations(make_repo):
    from repowise.server.mcp_server._change_health import health_delta_block

    block = health_delta_block(compare(split(make_repo)), revspec="HEAD")

    assert block["moved_to"] == {"tool.py": ["tool/a.py", "tool/b.py", "tool/c.py"]}
    assert "skipped" not in block


def unnamed(path: str, line: int) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="error_handling",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name=None,
        line_start=line,
        line_end=line,
        details={},
        health_impact=0.2,
        reason="bare except",
        dimension="defect",
    )


def test_a_finding_with_no_symbol_moves_with_the_symbol_whose_lines_hold_it():
    moves = MoveMap(
        MappingProxyType({("pkg/run.py", "run"): ("tool.py", "run")}),
        spans=MappingProxyType({("pkg/run.py", "run"): (1, 30), ("tool.py", "run"): (200, 229)}),
    )
    base = [unnamed("tool.py", 205), unnamed("tool.py", 220), unnamed("tool.py", 400)]
    head = [unnamed("pkg/run.py", 21), unnamed("pkg/run.py", 6)]

    result = FindingMatcher(moves=moves).match(base, head)

    pairs = {m.head.line_start: m.base.line_start for m in result.matched if m.base}
    assert pairs == {6: 205, 21: 220}
    assert [m.kind for m in result.matched] == ["moved", "moved"]
    assert [f.line_start for f in result.resolved] == [400]

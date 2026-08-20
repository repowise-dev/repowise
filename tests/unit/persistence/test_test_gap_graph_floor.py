"""A test reaching a file in the graph stops it being called a test gap.

Issue #1740: two filename heuristics answered "does this file have a test" and
disagreed - ``pr_blast._find_test_gaps`` (substring match over every test path)
and ``assessment._check_test_gap`` (SQL LIKE over the same). Both now consult
the graph before falling back to a name, so the answer they share comes from a
recorded edge rather than from whichever pattern each happened to implement.

Pinned here for both call sites at once, because the defect was precisely that
they were tested and maintained apart.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server.tool_risk.assessment import _check_test_gap
from tests.unit.persistence.helpers import insert_repo


async def _seed(session, edges, nodes):
    repo = await insert_repo(session)
    for path, is_test in nodes:
        session.add(
            GraphNode(repository_id=repo.id, node_id=path, node_type="file", is_test=is_test)
        )
    for src, dst in edges:
        session.add(
            GraphEdge(
                repository_id=repo.id,
                source_node_id=src,
                target_node_id=dst,
                edge_type="imports",
            )
        )
    await session.commit()
    return repo


# The shape the naming convention cannot see: the test is named for what it
# checks, not for the file it checks it in.
_BEHAVIOUR_NAMED = (
    [("tests/test_round_trips_cleanly.py", "src/parser.py")],
    [("tests/test_round_trips_cleanly.py", True), ("src/parser.py", False)],
)


async def test_pr_blast_does_not_call_a_reached_file_a_gap(async_session):
    repo = await _seed(async_session, *_BEHAVIOUR_NAMED)
    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    assert await analyzer._find_test_gaps(["src/parser.py"]) == []


async def test_assessment_does_not_call_a_reached_file_a_gap(async_session):
    repo = await _seed(async_session, *_BEHAVIOUR_NAMED)
    assert await _check_test_gap(async_session, repo.id, "src/parser.py") is False


@pytest.mark.parametrize("check", ["pr_blast", "assessment"])
async def test_a_file_nothing_reaches_is_still_a_gap(async_session, check):
    """The floor only clears files with evidence; it does not clear everything."""
    repo = await _seed(
        async_session,
        [("tests/test_round_trips_cleanly.py", "src/parser.py")],
        [
            ("tests/test_round_trips_cleanly.py", True),
            ("src/parser.py", False),
            ("src/lonely.py", False),
        ],
    )
    if check == "pr_blast":
        analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
        assert await analyzer._find_test_gaps(["src/lonely.py"]) == ["src/lonely.py"]
    else:
        assert await _check_test_gap(async_session, repo.id, "src/lonely.py") is True


async def test_a_co_change_edge_does_not_clear_a_gap(async_session):
    """Files that change together are not files that test each other."""
    repo = await insert_repo(async_session)
    for path, is_test in (("tests/test_x.py", True), ("src/lonely.py", False)):
        async_session.add(
            GraphNode(repository_id=repo.id, node_id=path, node_type="file", is_test=is_test)
        )
    async_session.add(
        GraphEdge(
            repository_id=repo.id,
            source_node_id="tests/test_x.py",
            target_node_id="src/lonely.py",
            edge_type="co_changes",
        )
    )
    await async_session.commit()

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    assert await analyzer._find_test_gaps(["src/lonely.py"]) == ["src/lonely.py"]
    assert await _check_test_gap(async_session, repo.id, "src/lonely.py") is True

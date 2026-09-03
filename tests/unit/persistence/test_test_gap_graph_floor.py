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


async def test_a_dispatch_named_module_is_not_a_gap_when_its_test_drops_the_prefix(
    async_session,
):
    """``tool_dead_code.py`` is tested by ``test_dead_code.py``, and the graph
    cannot say so: the test imports it through a package re-export and the
    barrel dispatches on by name. The filename fallback is the only signal left,
    so it has to try the stem the test is actually named for."""
    repo = await _seed(
        async_session,
        [],
        [
            ("tests/test_dead_code.py", True),
            ("tests/test_risk.py", True),
            ("src/mcp/tool_dead_code.py", False),
            ("src/mcp/get_risk.py", False),
        ],
    )
    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    assert await analyzer._find_test_gaps(["src/mcp/tool_dead_code.py", "src/mcp/get_risk.py"]) == []


async def test_stripping_the_prefix_does_not_clear_an_unrelated_file(async_session):
    """Widening the evidence must not lower the bar to "some test exists"."""
    repo = await _seed(
        async_session,
        [],
        [("tests/test_dead_code.py", True), ("src/mcp/tool_search.py", False)],
    )
    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    assert await analyzer._find_test_gaps(["src/mcp/tool_search.py"]) == ["src/mcp/tool_search.py"]


def test_a_stripped_stem_too_short_to_mean_anything_is_not_offered():
    """``get_id.py`` must not be cleared by every ``test_identity.py`` around."""
    from repowise.core.analysis.pr_blast import test_name_stems

    assert test_name_stems("tool_dead_code") == [("tool_dead_code", False), ("dead_code", True)]
    assert test_name_stems("get_id") == [("get_id", False)]
    assert test_name_stems("service") == [("service", False)]


async def test_a_stripped_stem_does_not_match_a_longer_word_it_prefixes(async_session):
    """``tool_repos`` strips to ``repos``, which is a prefix of ``repository``.

    The full stem keeps its substring match; a stripped one has to be named
    exactly, or widening the evidence would quietly become "some test exists".
    """
    repo = await _seed(
        async_session,
        [],
        [("tests/test_repository_head_commit.py", True), ("src/mcp/tool_repos.py", False)],
    )
    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    assert await analyzer._find_test_gaps(["src/mcp/tool_repos.py"]) == ["src/mcp/tool_repos.py"]

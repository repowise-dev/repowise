"""Coverage-backed test-gap detection in PRBlastRadiusAnalyzer._find_test_gaps.

Coverage-backed gaps: a file with a per-test coverage row is coverage-proven tested
(never a gap, even if no test file matches its NAME); a file the map has no data
for falls back to the filename pattern (honest "unknown", never asserted
untested). Seeds graph_nodes + a test_coverage row and pins both paths.
"""

from __future__ import annotations

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.persistence.crud import save_test_coverage
from repowise.core.persistence.models import GraphNode
from tests.unit.persistence.helpers import insert_repo


async def _node(session, repo_id, node_id, *, is_test=False):
    session.add(GraphNode(repository_id=repo_id, node_id=node_id, is_test=is_test))


async def test_coverage_proven_file_is_not_a_gap(async_session):
    repo = await insert_repo(async_session)
    # weirdly.py is covered by a test whose NAME does not match it - the
    # filename heuristic would wrongly call it a gap; coverage clears it.
    await _node(async_session, repo.id, "src/weirdly.py")
    await _node(async_session, repo.id, "src/uncovered.py")
    await _node(async_session, repo.id, "tests/test_misc.py", is_test=True)
    await save_test_coverage(
        async_session,
        repo.id,
        [
            TestCoverage(
                test_id="tests/test_misc.py::test_it",
                file_path="src/weirdly.py",
                covered_lines=[1, 2],
                source_format="coverage.py",
                test_file="tests/test_misc.py",
            )
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    gaps = await analyzer._find_test_gaps(["src/weirdly.py", "src/uncovered.py"])

    # weirdly.py: coverage-proven tested -> not a gap.
    assert "src/weirdly.py" not in gaps
    # uncovered.py: no coverage row AND no name-matched test -> still a gap.
    assert "src/uncovered.py" in gaps


async def test_filename_fallback_when_map_has_no_data(async_session):
    repo = await insert_repo(async_session)
    # No coverage rows at all -> pure filename fallback (pre-Phase-3 behavior):
    # foo.py has a name-matched test, bar.py does not.
    await _node(async_session, repo.id, "src/foo.py")
    await _node(async_session, repo.id, "src/bar.py")
    await _node(async_session, repo.id, "tests/test_foo.py", is_test=True)
    await async_session.commit()

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    gaps = await analyzer._find_test_gaps(["src/foo.py", "src/bar.py"])

    assert "src/foo.py" not in gaps  # name-matched test -> not a gap
    assert "src/bar.py" in gaps  # no test, no coverage -> gap

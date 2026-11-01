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


async def test_file_tests_names_the_evidence_per_file(async_session):
    from repowise.core.analysis.pr_blast import FileTests, file_tests

    repo = await insert_repo(async_session)
    await _node(async_session, repo.id, "src/weirdly.py")
    await _node(async_session, repo.id, "src/foo.py")
    await _node(async_session, repo.id, "src/bar.py")
    await _node(async_session, repo.id, "tests/test_foo.py", is_test=True)
    await save_test_coverage(
        async_session,
        repo.id,
        [
            TestCoverage(
                test_id="tests/test_misc.py::test_it",
                file_path="src/weirdly.py",
                covered_lines=[1],
                source_format="coverage.py",
                test_file="tests/test_misc.py",
            ),
            TestCoverage(
                test_id="test_dynamic",
                file_path="src/weirdly.py",
                covered_lines=[2],
                source_format="coverage.py",
            ),
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    rows = await file_tests(
        async_session,
        repo.id,
        ["src/weirdly.py", "src/foo.py", "src/bar.py", "tests/test_foo.py", "pyproject.toml"],
    )
    assert rows == {
        "src/weirdly.py": FileTests("tested", covered_by=("test_dynamic", "tests/test_misc.py")),
        "src/foo.py": FileTests("tested", named_by=("tests/test_foo.py",)),
        "src/bar.py": FileTests("untested"),
        "tests/test_foo.py": FileTests("test"),
        "pyproject.toml": FileTests("not_code"),
    }


async def test_a_module_a_test_loads_through_its_package_is_not_a_gap(async_session):
    """``test_why`` imports the ``tool_why`` package; its ``__init__`` imports
    ``tool.py``, which imports ``workspace.py``. A file only an untested page
    imports stays a gap, and a test the index has not seen yet is a test."""
    from repowise.core.analysis.pr_blast import FileTests, file_tests
    from repowise.core.persistence.models import GraphEdge

    repo = await insert_repo(async_session)
    nodes = {
        "tests/test_why.py": True,
        "src/server.py": False,
        "src/tool_why/__init__.py": False,
        "src/tool_why/tool.py": False,
        "src/tool_why/workspace.py": False,
        "web/app/page.tsx": False,
        "web/components/view.tsx": False,
    }
    for node, is_test in nodes.items():
        await _node(async_session, repo.id, node, is_test=is_test)
    for src, dst in [
        ("tests/test_why.py", "src/tool_why/__init__.py"),
        ("src/server.py", "src/tool_why/__init__.py"),
        ("src/tool_why/__init__.py", "src/tool_why/tool.py"),
        ("src/tool_why/tool.py", "src/tool_why/workspace.py"),
        ("web/app/page.tsx", "web/components/view.tsx"),
    ]:
        async_session.add(
            GraphEdge(
                repository_id=repo.id, source_node_id=src, target_node_id=dst, edge_type="imports"
            )
        )
    await async_session.commit()

    rows = await file_tests(
        async_session,
        repo.id,
        ["src/tool_why/workspace.py", "web/components/view.tsx", "tests/unit/test_new.py"],
    )
    assert rows == {
        "src/tool_why/workspace.py": FileTests("tested", reached_by=("tests/test_why.py",)),
        "web/components/view.tsx": FileTests("untested"),
        "tests/unit/test_new.py": FileTests("test"),
    }

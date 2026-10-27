"""``read_indirect_causes`` against real index rows: per-test coverage and the graph."""

from __future__ import annotations

import json

from repowise.core.analysis.patch_coverage import (
    IndirectCause,
    IndirectChange,
    read_indirect_causes,
)
from repowise.core.persistence.models import GraphEdge, GraphNode, TestCoverageEntry
from tests.unit.persistence.helpers import insert_repo


async def _index(session, repo_id: str) -> None:
    """tests/test_login.py calls ``login`` in src/auth.py, which src/cli.py imports
    and src/api.py calls; tests/test_util.py ran src/util.py (per-test rows)."""
    files = {
        "src/auth.py": False,
        "src/util.py": False,
        "src/cli.py": False,
        "src/api.py": False,
        "tests/test_login.py": True,
    }
    session.add_all(
        [
            *(
                GraphNode(repository_id=repo_id, node_id=path, node_type="file", is_test=is_test)
                for path, is_test in files.items()
            ),
            *(
                GraphEdge(
                    repository_id=repo_id, source_node_id=src, target_node_id=dst, edge_type=etype
                )
                for src, dst, etype in (
                    ("src/auth.py", "src/auth.py::login", "defines"),
                    ("tests/test_login.py", "tests/test_login.py::test_it", "defines"),
                    ("tests/test_login.py::test_it", "src/auth.py::login", "calls"),
                    ("src/cli.py", "src/auth.py", "imports"),
                    ("src/api.py::handler", "src/auth.py::login", "calls"),
                )
            ),
            TestCoverageEntry(
                repository_id=repo_id,
                test_id="tests/test_util.py::test_x",
                test_file="tests/test_util.py",
                source_file="src/util.py",
                covered_lines_json=json.dumps([3, 4]),
                source_format="coverage.py",
            ),
        ]
    )
    await session.flush()


def _lost(path: str) -> IndirectChange:
    return IndirectChange(path, ((3, 4),), 0, 100.0, 50.0, "changed")


async def test_each_file_names_the_changed_files_that_explain_it(async_session):
    repo = await insert_repo(async_session)
    await _index(async_session, repo.id)
    indirect = [
        _lost("src/auth.py"),
        _lost("src/util.py"),
        _lost("src/other.py"),
        # Only gained coverage: nothing to explain.
        IndirectChange("src/gained.py", (), 2, 50.0, 100.0, "changed"),
    ]
    changed = {
        "tests/test_login.py",
        "tests/test_util.py",
        "tests/test_other.py",
        "tests/test_auth.py",
        "src/cli.py",
        "src/api.py",
    }
    deleted = {"tests/test_util.py", "tests/test_other.py", "tests/test_auth.py"}

    causes = await read_indirect_causes(async_session, repo.id, indirect, changed, deleted)

    assert causes == {
        # Inferred from the call graph, plus the changed importer and caller. The
        # deleted test named for the file is added although the graph named one.
        "src/auth.py": (
            IndirectCause("test_modified", "tests/test_login.py", "graph"),
            IndirectCause("test_deleted", "tests/test_auth.py", "name"),
            IndirectCause("dependent_changed", "src/api.py", "graph"),
            IndirectCause("dependent_changed", "src/cli.py", "graph"),
        ),
        # Measured: the deleted test's per-test rows ran the file.
        "src/util.py": (IndirectCause("test_deleted", "tests/test_util.py", "per_test"),),
        # The index no longer knows the deleted test: paired by name.
        "src/other.py": (IndirectCause("test_deleted", "tests/test_other.py", "name"),),
    }


async def test_a_failed_read_leaves_causes_unassessed(async_session):
    causes = await read_indirect_causes(
        object(), "r", [_lost("src/a.py")], {"tests/test_a.py"}, set()
    )
    assert causes is None

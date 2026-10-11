"""``read_test_hints`` against real index rows: symbols, per-test coverage, the graph."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace

from repowise.core.analysis.health.coverage import file_coverage
from repowise.core.analysis.patch_coverage import (
    PatchScope,
    compute_patch_coverage,
    read_test_hints,
)
from repowise.core.persistence.models import GraphEdge, GraphNode, TestCoverageEntry, WikiSymbol
from tests.unit.persistence.helpers import insert_repo


def _symbol(repo_id: str, path: str, name: str, start: int, end: int) -> WikiSymbol:
    return WikiSymbol(
        repository_id=repo_id,
        file_path=path,
        symbol_id=f"{path}::{name}",
        name=name.rsplit(".", 1)[-1],
        qualified_name=name,
        kind="function",
        start_line=start,
        end_line=end,
    )


async def _index(session, repo_id: str) -> None:
    """src/auth.py: ``login`` (10-20) and ``logout`` (22-30).

    tests/test_login.py calls ``login``; tests/test_io.py only imports the file;
    src/util.py has a per-test row from tests/test_util.py near line 5.
    """
    session.add_all(
        [
            _symbol(repo_id, "src/auth.py", "login", 10, 20),
            _symbol(repo_id, "src/auth.py", "logout", 22, 30),
            *(
                GraphNode(repository_id=repo_id, node_id=path, node_type="file", is_test=is_test)
                for path, is_test in (
                    ("src/auth.py", False),
                    ("src/util.py", False),
                    ("tests/test_login.py", True),
                    ("tests/test_io.py", True),
                )
            ),
            *(
                GraphEdge(
                    repository_id=repo_id, source_node_id=src, target_node_id=dst, edge_type=etype
                )
                for src, dst, etype in (
                    ("tests/test_login.py", "tests/test_login.py::test_it", "defines"),
                    ("tests/test_login.py::test_it", "src/auth.py::login", "calls"),
                    ("tests/test_io.py", "src/auth.py", "imports"),
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


async def test_each_range_gets_its_strongest_evidence(async_session):
    repo = await insert_repo(async_session)
    await _index(async_session, repo.id)
    changed = {"src/auth.py": {12, 25}, "src/util.py": {6}, "src/done.py": {1}}
    pc = compute_patch_coverage(
        changed,
        {
            "src/auth.py": file_coverage("src/auth.py", [], [12, 25]),
            "src/util.py": file_coverage("src/util.py", [], [6]),
            "src/done.py": file_coverage("src/done.py", [1], [1]),
        },
        scope=PatchScope(freshness="current"),
    )

    hints = await read_test_hints(async_session, repo.id, pc)

    # A fully covered file has nothing to hint.
    assert set(hints) == {"src/auth.py", "src/util.py"}
    login, logout = hints["src/auth.py"]
    assert (login.symbol, login.basis, login.tests) == ("login", "call_graph", ("tests/test_login.py",))
    # No test calls logout; the file's importer is the fallback, labelled so.
    assert (logout.symbol, logout.basis, logout.tests) == (
        "logout",
        "import_graph",
        ("tests/test_io.py",),
    )
    (util,) = hints["src/util.py"]
    assert (util.symbol, util.basis, util.tests) == (None, "per_test", ("tests/test_util.py",))

    # Coverage not current for this change: per-test line numbers may describe
    # other code, so they are not evidence.
    stale = await read_test_hints(async_session, repo.id, replace(pc, scope=PatchScope()))
    assert stale["src/util.py"][0].basis == "none"


async def test_an_empty_index_hints_none(async_session):
    repo = await insert_repo(async_session)
    pc = compute_patch_coverage({"a.py": {1}}, {"a.py": file_coverage("a.py", [], [1])})

    (hint,) = (await read_test_hints(async_session, repo.id, pc))["a.py"]
    assert (hint.symbol, hint.basis, hint.tests, hint.total) == (None, "none", (), 0)


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_spans_are_moved_to_the_working_tree(async_session, tmp_path):
    """The index saw ``login`` at 1-3; a new function now sits above it."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "auth.py").write_text("def login():\n    a = 1\n    return a\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    indexed = _git(tmp_path, "rev-parse", "HEAD")
    repo = await insert_repo(async_session, local_path=str(tmp_path))
    repo.head_commit = indexed
    async_session.add(_symbol(repo.id, "auth.py", "login", 1, 3))
    await async_session.flush()
    (tmp_path / "auth.py").write_text(
        "def fresh():\n    return 0\n\n\ndef login():\n    a = 2\n    return a\n",
        encoding="utf-8",
    )
    pc = compute_patch_coverage(
        {"auth.py": {2, 6}}, {"auth.py": file_coverage("auth.py", [], [2, 6])}
    )

    raw = await read_test_hints(async_session, repo.id, pc)
    moved = await read_test_hints(
        async_session, repo.id, pc, repo_path=str(tmp_path), working_tree=True
    )

    # As stored, line 2 would read as login; moved, it is new code and line 6 is login.
    assert [h.symbol for h in raw["auth.py"]] == ["login", None]
    assert [h.symbol for h in moved["auth.py"]] == [None, "login"]


async def test_a_symbol_keyed_walk_ranks_the_test_named_for_its_file_first(async_session):
    from repowise.core.analysis.test_reachability import tests_reaching_by_tier

    repo = await insert_repo(async_session)
    for path, is_test in (("src/auth.py", False), ("tests/a_other.py", True), ("tests/test_auth.py", True)):
        async_session.add(
            GraphNode(repository_id=repo.id, node_id=path, node_type="file", is_test=is_test)
        )
    for test in ("tests/a_other.py", "tests/test_auth.py"):
        for src, dst, etype in (
            (test, f"{test}::t", "defines"),
            (f"{test}::t", "src/auth.py::login", "calls"),
        ):
            async_session.add(
                GraphEdge(repository_id=repo.id, source_node_id=src, target_node_id=dst, edge_type=etype)
            )
    await async_session.flush()

    sid = "src/auth.py::login"
    found = await tests_reaching_by_tier(
        async_session, repo.id, [sid], import_depth=0, symbol_seeds={sid: {sid}}
    )
    assert found[sid].tests == ["tests/test_auth.py", "tests/a_other.py"]

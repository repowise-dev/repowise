"""``tests_covering_changes`` answers a whole change in one bulk read (issue #3345).

It must return, file for file, what a ``tests_covering`` call per file returns,
with the line narrowing intact, while issuing one query per batch of files
instead of one per file.
"""

from __future__ import annotations

import json

from sqlalchemy import event

from repowise.core.persistence.crud._shared import _BATCH_SIZE
from repowise.core.persistence.crud.analysis.coverage_map import (
    tests_covering as covering,
)
from repowise.core.persistence.crud.analysis.coverage_map import (
    tests_covering_changes as covering_changes,
)
from repowise.core.persistence.models import TestCoverageEntry
from tests.unit.persistence.helpers import insert_repo


def _entry(repo_id: str, source: str, test: str, lines, test_file: str | None = None):
    return TestCoverageEntry(
        repository_id=repo_id,
        test_id=f"{test_file or 'tests/test_x.py'}::{test}",
        test_file=test_file,
        source_file=source,
        covered_lines_json=lines if isinstance(lines, str) else json.dumps(lines),
        source_format="coverage.py",
    )


async def _per_file(session, repo_id, changed):
    return {f: await covering(session, repo_id, f, lines=ls) for f, ls in changed.items()}


def _key(rows):
    return sorted(rows, key=lambda r: (r["test_id"], r["test_file"] or ""))


async def test_matches_a_per_file_loop_with_and_without_lines(async_session):
    repo = await insert_repo(async_session)
    other = await insert_repo(async_session, name="other", local_path="/tmp/other")
    async_session.add_all(
        [
            _entry(repo.id, "src/a.py", "t1", [1, 2, 3], "tests/test_a.py"),
            _entry(repo.id, "src/a.py", "t2", [10, 11], "tests/test_a2.py"),
            _entry(repo.id, "src/a.py", "pathless", [2]),
            _entry(repo.id, "src/b.py", "t3", [5], "tests/test_b.py"),
            _entry(repo.id, "src/b.py", "broken", "not json", "tests/test_b.py"),
            _entry(repo.id, "src/c.py", "t4", [7, 8], "tests/test_c.py"),
            # Another repository's rows never leak into this one's answer.
            _entry(other.id, "src/a.py", "elsewhere", [1, 2, 3], "tests/test_a.py"),
        ]
    )
    await async_session.flush()
    changed = {
        "src/a.py": {2, 11},  # narrowed: t1 -> [2], t2 -> [11], pathless -> [2]
        "src/b.py": None,  # every row, the unreadable one with no lines
        "src/c.py": {1},  # covered file, uncovered change -> []
        "src/missing.py": {1},  # no rows at all -> []
    }

    bulk = await covering_changes(async_session, repo.id, changed)
    expected = await _per_file(async_session, repo.id, changed)

    assert set(bulk) == set(changed)
    for path in changed:
        assert _key(bulk[path]) == _key(expected[path]), path
    assert {r["test_id"]: r["covered_lines"] for r in bulk["src/a.py"]} == {
        "tests/test_a.py::t1": [2],
        "tests/test_a2.py::t2": [11],
        "tests/test_x.py::pathless": [2],
    }
    assert bulk["src/c.py"] == []
    assert bulk["src/missing.py"] == []


async def test_more_files_than_one_batch_are_all_answered(async_session):
    repo = await insert_repo(async_session)
    paths = [f"src/m{i}.py" for i in range(_BATCH_SIZE + 20)]
    async_session.add_all(_entry(repo.id, p, "t", [1, 2], "tests/test_m.py") for p in paths)
    await async_session.flush()
    changed = {p: ({2} if i % 2 else None) for i, p in enumerate(paths)}

    bulk = await covering_changes(async_session, repo.id, changed)

    assert bulk == await _per_file(async_session, repo.id, changed)
    assert bulk[paths[-1]] == [
        {
            "test_id": "tests/test_m.py::t",
            "test_file": "tests/test_m.py",
            "covered_lines": [2],
            "source_format": "coverage.py",
        }
    ]


async def test_reads_the_whole_change_in_one_query(async_session):
    repo = await insert_repo(async_session)
    paths = [f"src/f{i}.py" for i in range(40)]
    async_session.add_all(_entry(repo.id, p, "t", [1], "tests/test_f.py") for p in paths)
    await async_session.flush()

    statements: list[str] = []
    engine = async_session.bind.sync_engine

    def _count(conn, cursor, statement, *args):
        if "test_coverage" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        await covering_changes(async_session, repo.id, {p: {1} for p in paths})
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert len(statements) == 1


async def test_nothing_changed_reads_nothing(async_session):
    repo = await insert_repo(async_session)

    assert await covering_changes(async_session, repo.id, {}) == {}

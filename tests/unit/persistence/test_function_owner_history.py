"""``crud.function_owner_history``: a function's owner and last change from its own lines."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.evidence.payloads import owner_history_payload
from repowise.core.persistence.crud import function_owner_history, upsert_git_function_blame_bulk
from repowise.core.persistence.models import GitCommit
from tests.unit.persistence.helpers import insert_repo

NEW = "a" * 40
OLD = "b" * 40


def _row(symbol_id: str, name: str, start: int, end: int, **over) -> dict:
    return {
        "symbol_id": symbol_id,
        "file_path": symbol_id.partition("::")[0],
        "function_name": name,
        "start_line": start,
        "end_line": end,
        "line_count": end - start + 1,
        "mod_count": 2,
        "owner_name": "Grace",
        "owner_email": "grace@x",
        "owner_line_pct": 0.6,
        "commit_shas_json": json.dumps([NEW, OLD]),
        **over,
    }


@pytest.mark.asyncio
async def test_rows_match_by_file_name_and_lines(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_function_blame_bulk(
        async_session,
        repo.id,
        [
            # A method's row is keyed ``path::name``; the symbol id names its class.
            _row("tree.go::getValue", "getValue", 418, 672),
            _row(
                "big.py::run", "run", 10, 20, history_only=True, commit_shas_json=json.dumps([OLD])
            ),
            # Same name, other lines: another function's row, never served.
            _row("a.py::__init__", "__init__", 60, 72),
        ],
    )
    async_session.add(
        GitCommit(
            repository_id=repo.id,
            sha=NEW,
            subject="fix(tree): grow the stack (#4852)",
            committed_at=datetime(2026, 10, 10, 9, tzinfo=UTC),
        )
    )
    await async_session.commit()

    got = await function_owner_history(
        async_session,
        repo.id,
        [
            ("tree.go::node::getValue", 418, 672),
            ("big.py::Job::run", 10, 20),
            ("a.py::Stream::__init__", 95, 103),
            ("c.py::missing", 1, 5),
        ],
    )

    assert got["tree.go::node::getValue"] == {
        "owner": {"name": "Grace", "line_pct": 0.6},
        "last_change": {
            "sha": NEW,
            "date": "2026-10-10",
            "subject": "fix(tree): grow the stack (#4852)",
            "pr": 4852,
        },
    }
    # A commit older than the indexed window: the sha alone.
    assert got["big.py::Job::run"]["last_change"] == {
        "sha": OLD,
        "date": None,
        "subject": None,
        "pr": None,
    }
    # No row of its own: no entry, so no owner line (never the file's owner).
    assert "a.py::Stream::__init__" not in got and "c.py::missing" not in got


def test_payload_without_owner_or_history() -> None:
    blame = SimpleNamespace(owner_name=None, owner_line_pct=None, commit_shas_json=None)
    assert owner_history_payload(blame, None) == {"owner": None, "last_change": None}

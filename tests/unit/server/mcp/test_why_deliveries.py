"""get_why replies ledger the decisions they delivered."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from repowise.server.mcp_server import tool_middleware
from repowise.server.mcp_server._why_deliveries import delivered_decision_ids

_SERVED = "a" * 32
_CANDIDATE = "b" * 32
_LINEAGE = "c" * 32


def _reply() -> dict:
    return {
        "decisions": [
            {"id": _SERVED, "title": "t", "authority": "accepted", "lineage": [{"id": _LINEAGE}]}
        ],
        "candidates": [{"id": _CANDIDATE, "title": "u", "authority": "candidate"}],
        "evidence": [{"id": "ev_1234"}],
    }


async def get_why(query: str | None = None) -> dict:  # the name the ledger keys on
    return _reply()


async def get_context(query: str | None = None) -> dict:
    return _reply()


def _why_rows(repo: Path) -> list[tuple]:
    db = repo / ".repowise" / "sessions" / "sessions.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT decision_id, node_id, surface, category, evaluated FROM injections "
            "ORDER BY node_id"
        ).fetchall()
    finally:
        conn.close()


def test_served_rows_are_the_ones_carrying_authority() -> None:
    assert delivered_decision_ids(_reply()) == [_SERVED, _CANDIDATE]


async def test_a_reply_ledgers_each_delivered_decision(monkeypatch, tmp_path: Path) -> None:
    from repowise.server.mcp_server import _state

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    result = await tool_middleware(get_why)(query="x")

    assert delivered_decision_ids(result) == [_SERVED, _CANDIDATE]
    assert _why_rows(tmp_path) == [
        (f"why:{_SERVED}", _SERVED, "why", "served", 1),
        (f"why:{_CANDIDATE}", _CANDIDATE, "why", "served", 1),
    ]


async def test_other_tools_and_unbound_servers_write_nothing(monkeypatch, tmp_path: Path) -> None:
    from repowise.server.mcp_server import _state

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    await tool_middleware(get_context)(query="x")
    assert _why_rows(tmp_path) == []

    monkeypatch.setattr(_state, "_repo_path", None)
    monkeypatch.chdir(tmp_path)
    await tool_middleware(get_why)(query="x")
    assert _why_rows(tmp_path) == []

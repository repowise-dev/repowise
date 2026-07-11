"""Read-after-served KPI tracking + the shared hook efficacy ledger.

The contract under test: repowise MCP responses' served line-ranges are
tracked per session, a Read substantially covered by them logs exactly one
measurement row (chars=0, never an injection), unknown tool-output shapes are
skipped rather than guessed, and the extended ledger migrates legacy sidecars
on both the hook side and the staging-store side.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from repowise.cli.commands.augment_cmd import served_reads
from repowise.cli.commands.augment_cmd.decision_inject import _claim_ledger
from repowise.cli.commands.augment_cmd.search import _log_search_firing

_TARGET = "src/core/models.py"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _full_read(num_lines: int = 60, rel: str = _TARGET) -> tuple[dict, dict]:
    return (
        {"file_path": rel},
        {"file": {"numLines": num_lines, "content": "x\n" * num_lines}},
    )


def _log_read(repo_root: Path, tool_input: dict, tool_output: dict, session_id="sess-1"):
    tool_input = {**tool_input, "file_path": str(repo_root / tool_input["file_path"])}
    served_reads._log_read_after_served(tool_input, tool_output, str(repo_root), session_id)


def _serve(repo_root: Path, start: int, end: int, session_id="sess-1", rel=_TARGET) -> None:
    payload = {
        "result": {
            "symbol_id": f"{rel}:{start}-{end}",
            "file": rel,
            "start_line": start,
            "end_line": end,
            "source": "   1\tx",
        }
    }
    served_reads._handle_mcp_read_post(
        [{"type": "text", "text": json.dumps(payload)}], str(repo_root), session_id
    )


def _ledger_rows(repo_root: Path) -> list[tuple]:
    db = repo_root / ".repowise" / "sessions" / "sessions.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT session_id, decision_id, surface, category, chars FROM injections"
        ).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Read-after-served
# ---------------------------------------------------------------------------


def test_covered_read_logs_one_measurement_row(tmp_path):
    repo = _repo(tmp_path)
    _serve(repo, 1, 60)

    _log_read(repo, *_full_read(num_lines=60))
    _log_read(repo, *_full_read(num_lines=60))  # once per file per session

    rows = _ledger_rows(repo)
    assert rows == [
        (
            "sess-1",
            f"read_enrich:read_after_served:{_TARGET}",
            "read_enrich",
            "read_after_served",
            0,
        )
    ]


def test_partially_served_read_is_not_logged(tmp_path):
    repo = _repo(tmp_path)
    _serve(repo, 1, 30)  # 50% of the window: below the coverage bar
    _log_read(repo, *_full_read(num_lines=60))
    assert _ledger_rows(repo) == []


def test_served_ranges_union_counts(tmp_path):
    repo = _repo(tmp_path)
    # Two disjoint serves covering 50/60 lines = 83% >= 80%.
    _serve(repo, 1, 30)
    _serve(repo, 41, 60)
    _log_read(repo, *_full_read(num_lines=60))
    assert any(r[3] == "read_after_served" for r in _ledger_rows(repo))


def test_partial_read_window_uses_offset(tmp_path):
    repo = _repo(tmp_path)
    _serve(repo, 100, 200)
    tool_input = {"file_path": str(repo / _TARGET), "offset": 110, "limit": 50}
    tool_output = {"file": {"numLines": 50, "content": "x\n" * 50}}
    served_reads._log_read_after_served(tool_input, tool_output, str(repo), "sess-1")
    assert any(r[3] == "read_after_served" for r in _ledger_rows(repo))


def test_unread_or_unknown_shapes_never_log(tmp_path):
    repo = _repo(tmp_path)
    _serve(repo, 1, 60)
    tool_input, _ = _full_read()
    # Unknown Read output shape: refuse to guess a window.
    _log_read(repo, tool_input, {"unexpected": True})
    # No session id: no dedup key, no row.
    _log_read(repo, *_full_read(num_lines=60), session_id="")
    assert _ledger_rows(repo) == []


def test_mcp_unknown_shapes_record_nothing(tmp_path):
    repo = _repo(tmp_path)
    served_reads._handle_mcp_read_post({"weird": 1}, str(repo), "sess-1")
    served_reads._handle_mcp_read_post("not json", str(repo), "sess-1")
    served_reads._handle_mcp_read_post(None, str(repo), "sess-1")
    state_path = repo / ".repowise" / ".augment-session.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert not state.get("served")


def test_served_ranges_parser_shapes():
    get_symbol = {
        "result": {
            "file": "a.py",
            "start_line": 10,
            "end_line": 40,
            "source": "src",
            "candidates": [
                {"file": "b.py", "start_line": 1, "end_line": 5, "source": "s"},
                {"file": "c.py", "start_line": 1, "end_line": 5},  # no source: skip
            ],
        }
    }
    assert served_reads._served_ranges(get_symbol) == [("a.py", 10, 40), ("b.py", 1, 5)]

    answer = {
        "result": {
            "symbol_bodies": [{"path": "d\\e.py", "lines": [5, 25], "source": "s"}],
            "quotes": [{"path": "f.py", "lines": [1, 3], "quote": "q"}],
        }
    }
    assert served_reads._served_ranges(answer) == [("d/e.py", 5, 25), ("f.py", 1, 3)]

    assert served_reads._served_ranges({"result": {"answer": "prose only"}}) == []
    assert served_reads._served_ranges([{"type": "text", "text": "{broken"}]) == []


# ---------------------------------------------------------------------------
# Shared ledger: search-surface logging + migration
# ---------------------------------------------------------------------------


def test_search_firing_logs_to_ledger(tmp_path):
    repo = _repo(tmp_path)
    _log_search_firing(repo, "sess-1", "rescue", "parse_yaml", "[repowise] rescue text")
    _log_search_firing(repo, "sess-1", "rescue", "parse_yaml", "[repowise] rescue text")  # dedup
    _log_search_firing(repo, "", "rescue", "parse_yaml", "text")  # no session id: skipped

    rows = _ledger_rows(repo)
    assert len(rows) == 1
    session_id, key, surface, category, chars = rows[0]
    assert (session_id, surface, category) == ("sess-1", "search", "rescue")
    assert key.startswith("search:rescue:")
    assert chars == len("[repowise] rescue text")


def test_claim_ledger_migrates_legacy_sidecar(tmp_path):
    repo = _repo(tmp_path)
    db_path = repo / ".repowise" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE injections (session_id TEXT NOT NULL, decision_id TEXT NOT NULL, "
        "node_id TEXT NOT NULL DEFAULT '', shown_at REAL NOT NULL, "
        "evaluated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (session_id, decision_id))"
    )
    con.execute("INSERT INTO injections (session_id, decision_id, shown_at) VALUES ('s', 'd', 1.0)")
    con.commit()
    con.close()

    claimed, count = _claim_ledger(
        repo,
        "sess-1",
        "read_enrich:read_after_served:x.py",
        node_id="x.py",
        surface="read_enrich",
        category="read_after_served",
        chars=0,
    )

    assert claimed is True
    assert count == 0  # chars=0 measurement rows never count as injections
    rows = _ledger_rows(repo)
    assert ("s", "d", "", "", 0) in rows  # legacy row got the defaults
    assert (
        "sess-1",
        "read_enrich:read_after_served:x.py",
        "read_enrich",
        "read_after_served",
        0,
    ) in rows


def test_staging_store_migrates_legacy_sidecar(tmp_path):
    from repowise.core.sessions.staging import SessionStagingStore

    db_path = tmp_path / "sessions.db"
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE injections (session_id TEXT NOT NULL, decision_id TEXT NOT NULL, "
        "node_id TEXT NOT NULL DEFAULT '', shown_at REAL NOT NULL, "
        "evaluated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (session_id, decision_id))"
    )
    con.commit()
    con.close()

    with SessionStagingStore(db_path) as store:
        cols = {row[1] for row in store._conn.execute("PRAGMA table_info(injections)")}
        assert {"surface", "category", "chars"} <= cols
        # Decision-surface reader ignores non-decision rows.
        store._conn.execute(
            "INSERT INTO injections (session_id, decision_id, shown_at, surface) "
            "VALUES ('s1', 'read_enrich:read_after_served:x.py', 1.0, 'read_enrich')"
        )
        store._conn.execute(
            "INSERT INTO injections (session_id, decision_id, shown_at, surface) "
            "VALUES ('s1', 'd-legacy', 1.0, '')"
        )
        store.commit()
        pending = store.unevaluated_injections(before=2.0)
        assert [p["decision_id"] for p in pending] == ["d-legacy"]

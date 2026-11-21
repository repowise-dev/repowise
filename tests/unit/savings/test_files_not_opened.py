"""Files not opened: one credit per (session, path), owned by the first event.

A later Read of the path in that session cancels the credit; an earlier one
does not. Tool calls answered count usable MCP, VS Code tool and hook events.
The SQL report and the pure builder agree on every one of these figures.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from repowise.core.distill.store import OmissionStore
from repowise.core.savings import schema
from repowise.core.savings.contracts import MAX_AVOIDED_FILES, SavingsEvent
from repowise.core.savings.correlation import file_session_key, scoped_idempotency_key
from repowise.core.savings.reporting import build_report
from repowise.core.savings.repository import SavingsRepository

REPO = "repo"
AS_OF = datetime.fromisoformat("2026-09-15T00:00:00+00:00")
_FILE_FIELDS = (
    "files_not_opened",
    "files_not_opened_tokens",
    "tool_calls_answered",
    "files_per_day",
    "files_per_agent",
    "files_per_surface",
)


def _event(
    index: int,
    *,
    surface: str = "mcp",
    files: object = (),
    session: str | None = "host-1",
    state: str = "success",
    day: str = "14",
) -> SavingsEvent:
    return SavingsEvent.from_mapping(
        {
            "idempotency_key": scoped_idempotency_key(REPO, surface, index),
            "occurred_at": f"2026-09-{day}T00:00:{index:02d}Z",
            "repository_id": REPO,
            "surface": surface,
            "integration": "claude_code",
            "agent": "claude_code",
            "operation": "get_context",
            "evidence_kind": "measured",
            "estimator": "fixture_v1",
            "token_unit": "estimated_tokens",
            "result_state": state,
            "is_usable": state == "success",
            "baseline_input_tokens": 100,
            "pre_budget_input_tokens": 100,
            "delivered_input_tokens": 40,
            "avoided_files": files,
            "host_session_id": session,
        }
    )


def _report(tmp_path: Path, events: list[SavingsEvent], reads=()):
    with OmissionStore(tmp_path / "omissions.db") as store:
        repository = SavingsRepository(store._conn)
        for event in events:
            repository.record_event(event)
        for session, path, at in reads:
            repository.record_file_read(file_session_key(session), path, at)
        sql = repository.report(REPO, as_of=AS_OF)
    pure = build_report(
        events,
        as_of=AS_OF,
        file_reads=[(file_session_key(s), p, at) for s, p, at in reads],
    )
    # One owner of the arithmetic: both builders agree field for field.
    assert {k: asdict(sql)[k] for k in _FILE_FIELDS} == {k: asdict(pure)[k] for k in _FILE_FIELDS}
    return sql


def test_a_path_is_credited_once_per_session_across_surfaces(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [
            _event(1, files=[("a.py", 500), ("b.py", 300)]),
            # The hook later collapses a Read of a.py in the same session.
            _event(2, surface="hook", files=[("a.py", 900)]),
            # Another session owns its own credit for the same path.
            _event(3, files=[("a.py", 500)], session="host-2"),
        ],
    )
    assert report.files_not_opened == 3
    # The first event owns a.py, so its 500 counts, not the hook's 900.
    assert report.files_not_opened_tokens == 1300
    assert report.tool_calls_answered == 3
    surfaces = {row["surface"]: row for row in report.files_per_surface}
    assert surfaces["mcp"]["files_not_opened"] == 3
    assert surfaces["hook"]["files_not_opened"] == 0
    assert surfaces["hook"]["tool_calls_answered"] == 1


def test_a_later_read_cancels_the_credit_and_an_earlier_one_does_not(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [_event(1, files=[("a.py", 500), ("b.py", 300)])],
        reads=[
            ("host-1", "a.py", "2026-09-14T00:05:00.000000Z"),
            ("host-1", "b.py", "2026-09-13T00:00:00.000000Z"),
            # A Read in another session cancels nothing here.
            ("host-2", "b.py", "2026-09-14T00:05:00.000000Z"),
        ],
    )
    assert report.files_not_opened == 1
    assert report.files_not_opened_tokens == 300


def test_a_read_keeps_its_latest_time(tmp_path: Path) -> None:
    key = file_session_key("host-1")
    with OmissionStore(tmp_path / "omissions.db") as store:
        repository = SavingsRepository(store._conn)
        repository.record_file_read(key, "a.py", "2026-09-14T00:05:00.000000Z")
        repository.record_file_read(key, "a.py", "2026-09-14T00:01:00.000000Z")
        rows = store._conn.execute("SELECT read_at FROM savings_file_reads").fetchall()
    assert rows == [("2026-09-14T00:05:00.000000Z",)]


def test_events_without_a_session_credit_per_event_and_errors_never(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [
            _event(1, files=[("a.py", 10)], session=None),
            _event(2, files=[("a.py", 10)], session=None),
            _event(3, files=[("c.py", 10)], state="error"),
            _event(4, surface="distill"),
        ],
    )
    assert report.files_not_opened == 2
    # The error is not answered and distill is command output, not a tool call.
    assert report.tool_calls_answered == 2


def test_the_per_event_list_is_capped_and_malformed_entries_skipped() -> None:
    files = [(f"f{i}.py", i) for i in range(MAX_AVOIDED_FILES + 10)]
    event = _event(1, files=[("", 1), ("x" * 600, 1), ("ok.py", -3), *files])
    assert len(event.avoided_files) == MAX_AVOIDED_FILES
    assert event.avoided_files[0] == ("ok.py", None)
    assert _event(2, files=[("a.py", 1)], state="error").avoided_files == ()


def test_a_broken_file_list_never_costs_the_event(monkeypatch) -> None:
    from repowise.core.savings import contracts

    def boom(_value):
        raise RuntimeError("forced")

    monkeypatch.setattr(contracts, "_avoided_files", boom)
    event = _event(1, files=[("a.py", 1)])
    assert event.avoided_files == ()
    assert event.saved_input_tokens == 60


def test_files_are_stored_as_paths_beside_the_event(tmp_path: Path) -> None:
    with OmissionStore(tmp_path / "omissions.db") as store:
        SavingsRepository(store._conn).record_event(_event(1, files=[("a.py", 5)]))
        rows = store._conn.execute(
            "SELECT path, read_tokens, length(session_key) FROM savings_event_files"
        ).fetchall()
    assert rows == [("a.py", 5, 71)]


def test_a_ledger_without_the_files_tables_gains_them_in_place(tmp_path: Path) -> None:
    db_path = tmp_path / "omissions.db"
    with OmissionStore(db_path) as store:
        SavingsRepository(store._conn).record_event(_event(1))
        store._conn.execute("DROP TABLE savings_event_files")
        store._conn.execute("DROP TABLE savings_file_reads")
        store._conn.execute("PRAGMA user_version=2")
    ro = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        # A read-only report on the old ledger works and credits no files.
        old = SavingsRepository(ro).report(REPO, as_of=AS_OF)
        assert (old.unique_events, old.files_not_opened, old.tool_calls_answered) == (1, 0, 1)
    finally:
        ro.close()
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        schema.initialize_savings_schema(conn)
        schema.initialize_savings_schema(conn)  # idempotent
        assert conn.execute("PRAGMA user_version").fetchone()[0] == schema.SAVINGS_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM savings_event_files").fetchone()[0] == 0
    finally:
        conn.close()


def test_an_event_still_lands_on_a_ledger_without_the_files_table(tmp_path: Path) -> None:
    """The hook writes on a raw connection, which may predate the files table."""
    db_path = tmp_path / "omissions.db"
    OmissionStore(db_path).close()
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("DROP TABLE savings_event_files")
        assert SavingsRepository(conn).record_event(_event(1, files=[("a.py", 5)]))
        assert conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 1
    finally:
        conn.close()


def test_the_hook_and_mcp_keys_agree_however_the_root_is_spelled(tmp_path: Path) -> None:
    """The hook notes a Read under its own spelling of the root; it still cancels."""
    from repowise.core.savings.recorder import record_file_read_on

    db_path = tmp_path / "omissions.db"
    with OmissionStore(db_path) as store:
        SavingsRepository(store._conn).record_event(_event(1, files=[("a.py", 5)]))
        assert record_file_read_on(store._conn, str(tmp_path) + "/", "host-1", "a.py")
        report = SavingsRepository(store._conn).report(REPO, as_of=datetime.now().astimezone())
    assert report.files_not_opened == 0

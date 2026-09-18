"""Transactional sidecar upgrade, concurrency, idempotency, and reports."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.core.savings import schema
from repowise.core.savings.contracts import OpportunityObservation, SavingsEvent
from repowise.core.savings.correlation import scoped_idempotency_key
from repowise.core.savings.repository import SavingsRepository
from repowise.core.sqlite_pragmas import apply_sqlite_pragmas

FIXTURE = Path(__file__).parents[2] / "fixtures" / "savings" / "mixed_agents_v1.json"


def _event(index: int, *, operation: str = "get_risk") -> SavingsEvent:
    return SavingsEvent.from_mapping(
        {
            "idempotency_key": scoped_idempotency_key("repo", "mcp", index),
            "occurred_at": f"2026-09-14T00:00:{index:02d}Z",
            "repository_id": "repo",
            "surface": "mcp",
            "integration": "codex",
            "agent": "codex",
            "session_id": f"session-{index}",
            "request_id": scoped_idempotency_key("repo", "request", index),
            "tool_call_id": None,
            "operation": operation,
            "evidence_kind": "measured",
            "confidence": 1.0,
            "estimator": "fixture_v1",
            "token_unit": "estimated_tokens",
            "result_state": "success",
            "is_usable": True,
            "baseline_input_tokens": 100,
            "pre_budget_input_tokens": 100,
            "delivered_input_tokens": 40,
            "baseline_output_tokens": None,
            "delivered_output_tokens": None,
            "omission_refs": [f"{index:012x}"],
        }
    )


def _seed_legacy(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE omissions (
            ref TEXT PRIMARY KEY, content BLOB NOT NULL, source TEXT NOT NULL,
            created_at REAL NOT NULL, original_tokens INTEGER NOT NULL,
            kept_tokens INTEGER NOT NULL, access_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE evidence_references (
            ref TEXT PRIMARY KEY, content BLOB NOT NULL, repository TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE savings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
            filter TEXT NOT NULL, source TEXT NOT NULL, command TEXT,
            raw_tokens INTEGER NOT NULL, distilled_tokens INTEGER NOT NULL
        );
        CREATE TABLE other_data (value TEXT NOT NULL);
        INSERT INTO omissions VALUES ('aaaaaaaaaaaa', X'010203', 'mcp:get_risk', 1, 9, 0, 4);
        INSERT INTO evidence_references VALUES ('ev_1', X'040506', 'repo', 2);
        INSERT INTO savings(created_at, filter, source, command, raw_tokens, distilled_tokens)
            VALUES (3, 'get_risk', 'mcp:get_risk', NULL, 100, 10);
        INSERT INTO other_data VALUES ('keep me');
        """
    )
    conn.close()


def test_clean_install_has_versioned_event_link_and_query_indexes(tmp_path: Path) -> None:
    store = OmissionStore(tmp_path / "omissions.db")
    try:
        assert (
            store._conn.execute("PRAGMA user_version").fetchone()[0]
            == schema.SAVINGS_SCHEMA_VERSION
        )
        tables = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "omissions",
            "evidence_references",
            "savings",
            "savings_events",
            "savings_event_omissions",
            "savings_event_correlations",
            "savings_opportunities",
        } <= tables
        indexes = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        assert {
            "idx_savings_events_repo_time",
            "idx_savings_events_surface_time",
            "idx_savings_correlations_hash",
            "idx_savings_event_omissions_ref",
        } <= indexes
    finally:
        store.close()


def test_old_sidecar_reset_is_scoped_and_preserves_recovery_bytes(tmp_path: Path) -> None:
    db_path = tmp_path / "omissions.db"
    _seed_legacy(db_path)
    store = OmissionStore(db_path)
    try:
        assert store._conn.execute("SELECT * FROM omissions").fetchall() == [
            ("aaaaaaaaaaaa", b"\x01\x02\x03", "mcp:get_risk", 1.0, 9, 0, 4)
        ]
        assert store._conn.execute("SELECT * FROM evidence_references").fetchall() == [
            ("ev_1", b"\x04\x05\x06", "repo", 2.0)
        ]
        assert store._conn.execute("SELECT COUNT(*) FROM savings").fetchone()[0] == 0
        assert store._conn.execute("SELECT * FROM other_data").fetchall() == [("keep me",)]
    finally:
        store.close()


def test_claimed_current_but_incomplete_sidecar_is_repaired(tmp_path: Path) -> None:
    db_path = tmp_path / "omissions.db"
    _seed_legacy(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version={schema.SAVINGS_SCHEMA_VERSION}")
    with OmissionStore(db_path) as store:
        assert store._conn.execute("SELECT COUNT(*) FROM savings").fetchone()[0] == 0
        assert store._conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 0
        assert store._conn.execute("SELECT content FROM omissions").fetchone()[0] == b"\x01\x02\x03"


def test_failed_upgrade_rolls_back_tables_rows_and_version(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "omissions.db"
    _seed_legacy(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    apply_sqlite_pragmas(conn, 5000)
    original = schema._apply_schema

    def fail_after_changes(connection: sqlite3.Connection) -> None:
        original(connection)
        raise RuntimeError("simulated upgrade failure")

    monkeypatch.setattr(schema, "_apply_schema", fail_after_changes)
    with pytest.raises(RuntimeError, match="simulated upgrade failure"):
        schema.initialize_savings_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM savings").fetchone()[0] == 1
    assert conn.execute("SELECT content FROM omissions").fetchone()[0] == b"\x01\x02\x03"
    assert conn.execute("SELECT content FROM evidence_references").fetchone()[0] == b"\x04\x05\x06"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='savings_events'"
        ).fetchone()[0]
        == 0
    )
    conn.close()


def test_two_concurrent_openers_upgrade_once(tmp_path: Path) -> None:
    db_path = tmp_path / "omissions.db"
    _seed_legacy(db_path)

    def open_and_read_version(_: int) -> int:
        with OmissionStore(db_path) as store:
            return store._conn.execute("PRAGMA user_version").fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        expected = schema.SAVINGS_SCHEMA_VERSION
        assert list(executor.map(open_and_read_version, range(2))) == [expected, expected]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 0


def test_concurrent_writers_and_retry_dedupe_are_atomic(tmp_path: Path) -> None:
    db_path = tmp_path / "omissions.db"
    OmissionStore(db_path).close()

    def write(index: int) -> bool:
        with OmissionStore(db_path) as store:
            return SavingsRepository(store._conn).record_event(_event(index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(write, range(20)))
    assert write(0) is False
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 20
        assert conn.execute("SELECT COUNT(*) FROM savings_event_omissions").fetchone()[0] == 20
        correlations = conn.execute(
            "SELECT kind, value_hash FROM savings_event_correlations"
        ).fetchall()
        assert len(correlations) == 40
        assert all(len(value_hash) == 71 for _, value_hash in correlations)
        assert not any("session-" in value_hash for _, value_hash in correlations)
        stored_ids = conn.execute(
            "SELECT session_id, request_id, tool_call_id FROM savings_events"
        ).fetchall()
        assert all(
            value is None or (value.startswith("sha256:") and len(value) == 71)
            for row in stored_ids
            for value in row
        )


def test_fixture_roundtrip_report_prices_each_historical_event(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with OmissionStore(tmp_path / "omissions.db") as store:
        repository = SavingsRepository(store._conn)
        inserted: dict[str, bool] = {}
        for record in payload["records"]:
            event = SavingsEvent.from_mapping(record, accept_event_id=True)
            inserted[record["attempt_id"]] = repository.record_event(event)
        for item in payload["opportunities"]:
            repository.record_opportunity(
                OpportunityObservation.from_mapping(item, repository_id="fixture-repo")
            )

        assert inserted["attempt-hook-claude-retry"] is False
        for scope, days in (("all", None), ("30d", 30), ("7d", 7)):
            report = repository.report(
                "fixture-repo", as_of=datetime.fromisoformat(payload["as_of"]), days=days
            )
            actual = asdict(report)
            for key, expected in payload["expected"][scope].items():
                if isinstance(expected, float):
                    assert actual[key] == pytest.approx(expected)
                else:
                    assert actual[key] == expected


def test_report_queries_are_read_only_and_breakdowns_are_bounded(tmp_path: Path) -> None:
    db_path = tmp_path / "omissions.db"
    with OmissionStore(db_path) as store:
        writer = SavingsRepository(store._conn)
        for index in range(10):
            writer.record_event(_event(index, operation=f"tool-{index}"))

    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    report = SavingsRepository(conn).report(
        "repo", as_of=datetime.fromisoformat("2026-09-14T01:00:00+00:00"), max_breakdowns=3
    )
    conn.close()

    assert report.unique_events == 10
    assert report.saved_output_tokens is None
    assert report.priced_saved_output_tokens == 0
    assert report.unpriced_saved_output_tokens == 0
    assert len(report.per_operation) == 3
    assert len(statements) == 3
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)

"""A sidecar a newer Repowise stamped: read what is known, never write the ledger.

A downgrade, or two versions side by side, leaves this build holding a store
whose ``user_version`` it does not know. Opening it must not fail the caller,
must not migrate or restamp the file, and must not add ledger rows; the
omission stash keeps working so markers still resolve.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.engine import distill_output
from repowise.core.distill.store import OmissionStore
from repowise.core.savings import recorder, schema
from repowise.core.savings.correlation import new_event_id, scoped_idempotency_key
from repowise.core.savings.service import load_report

_LEDGER_TABLES = (
    "savings",
    "savings_events",
    "savings_event_omissions",
    "savings_event_correlations",
    "savings_opportunities",
    "savings_event_files",
    "savings_file_reads",
)


def _event(**overrides: object) -> dict:
    event_id = new_event_id()
    return {
        "event_id": event_id,
        "idempotency_key": scoped_idempotency_key("repo", "mcp", event_id),
        "occurred_at": datetime.now(UTC),
        "surface": "mcp",
        "integration": "claude_code",
        "agent": "claude_code",
        "operation": "get_risk",
        "evidence_kind": "measured",
        "estimator": "chars_per_token_floor_v1",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": 100,
        "pre_budget_input_tokens": 100,
        "delivered_input_tokens": 40,
        **overrides,
    }


def _stamp(db: Path, version: int) -> None:
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute(f"PRAGMA user_version={version}")
    finally:
        conn.close()


def _ledger_state(db: Path) -> tuple:
    """Version, schema and every ledger table's row count, read without writing."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        ddl = tuple(conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name"))
        counts = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in _LEDGER_TABLES
        )
    finally:
        conn.close()
    return version, ddl, counts


@pytest.fixture
def newer(tmp_path: Path) -> Path:
    """An opted-in repository whose sidecar holds one event and is stamped newer."""
    db = recorder.sidecar_path(tmp_path)
    OmissionStore(db).close()
    assert recorder.record_event(tmp_path, _event()) is True
    _stamp(db, schema.SAVINGS_SCHEMA_VERSION + 1)
    return tmp_path


def test_initialize_leaves_a_newer_store_alone(newer: Path) -> None:
    db = recorder.sidecar_path(newer)
    before = _ledger_state(db)
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        assert schema.is_newer_store(conn) is True
        assert schema.initialize_savings_schema(conn) is False
    finally:
        conn.close()
    assert _ledger_state(db) == before


def test_initialize_reports_a_current_store_writable(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "s.db", isolation_level=None)
    try:
        assert schema.initialize_savings_schema(conn) is True
        assert schema.initialize_savings_schema(conn) is True
        assert schema.is_newer_store(conn) is False
    finally:
        conn.close()


def test_every_ledger_write_is_dropped_on_a_newer_store(newer: Path) -> None:
    db = recorder.sidecar_path(newer)
    before = _ledger_state(db)

    assert recorder.record_event(newer, _event()) is False
    assert (
        recorder.record_opportunity(
            newer,
            {
                "observation_id": new_event_id(),
                "occurred_at": datetime.now(UTC),
                "integration": "unknown",
                "kind": "hook_surface_disabled:read",
                "estimated_potential_input_tokens": 10,
            },
        )
        is False
    )
    assert recorder.relocate_ledger(newer / "moved") == 0
    with OmissionStore(db) as store:
        assert recorder.record_event_in(store, newer, _event()) is False
        store.record_saving(
            filter_name="git_log", source="cli", command=None, raw_tokens=9, distilled_tokens=1
        )
        assert store.savings().adopt_rows("elsewhere") == 0
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        assert recorder.record_event_on(conn, newer, _event()) is False
        recorder.record_file_read_on(conn, newer, "session", "a.py")
    finally:
        conn.close()

    assert _ledger_state(db) == before


def test_the_report_reads_a_newer_store(newer: Path) -> None:
    report = load_report(newer)
    assert report is not None
    assert report.unique_events == 1
    assert report.saved_input_tokens == 60


def test_distill_output_is_the_same_on_a_newer_store(tmp_path: Path) -> None:
    """The omission stash still takes the content, so the marker still resolves."""
    output = "\n".join(f"commit {i:040x}\nAuthor: a\n\n    message {i}\n" for i in range(80))
    results = {}
    for name, version in (("current", schema.SAVINGS_SCHEMA_VERSION), ("newer", 99)):
        db = recorder.sidecar_path(tmp_path / name)
        OmissionStore(db).close()
        _stamp(db, version)
        with OmissionStore(db) as store:
            results[name] = distill_output(output, command="git log", store=store)
            assert store.get(results[name].ref) == output
    assert results["current"].distilled is True
    assert results["newer"].text == results["current"].text
    db = recorder.sidecar_path(tmp_path / "newer")
    version, _ddl, counts = _ledger_state(db)
    assert version == 99
    assert counts == (0,) * len(_LEDGER_TABLES)

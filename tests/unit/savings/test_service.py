"""The one report service every first-party savings surface reads through."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from repowise.core.distill.store import OmissionStore
from repowise.core.savings import recorder
from repowise.core.savings.correlation import new_event_id, scoped_idempotency_key
from repowise.core.savings.reporting import build_report
from repowise.core.savings.service import load_report


def _sidecar(repo: Path) -> None:
    OmissionStore(repo / ".repowise" / "omissions" / "omissions.db").close()


def _record(repo: Path, *, baseline: int, surface: str = "mcp", agent: str = "codex") -> None:
    event_id = new_event_id()
    assert recorder.record_event(
        repo,
        {
            "event_id": event_id,
            "idempotency_key": scoped_idempotency_key(str(repo), surface, event_id),
            "occurred_at": datetime.now(UTC),
            "surface": surface,
            "integration": agent,
            "agent": agent,
            "operation": "get_answer",
            "evidence_kind": "measured",
            "estimator": "chars_per_token_floor_v1",
            "token_unit": "estimated_tokens",
            "result_state": "success",
            "is_usable": True,
            "baseline_input_tokens": baseline,
            "pre_budget_input_tokens": baseline,
            "delivered_input_tokens": 0,
        },
    )


def test_a_repository_with_no_sidecar_reports_nothing_rather_than_zero(
    tmp_path: Path,
) -> None:
    """Absent and zero are different claims, and the callers render them differently."""
    assert load_report(tmp_path) is None
    assert load_report(None) is None


def test_an_initialized_but_empty_sidecar_reports_a_measured_zero(tmp_path: Path) -> None:
    _sidecar(tmp_path)
    report = load_report(tmp_path)
    assert report is not None
    assert report.unique_events == 0
    assert report.saved_input_tokens == 0
    assert report.last_event_at is None


def test_the_reader_asks_with_the_same_repository_id_the_recorder_wrote(
    tmp_path: Path,
) -> None:
    """The recorder files events under the path it wrote to. If the reader asks
    with a different string it finds nothing and reports a confident zero --
    which looks exactly like a working report of a quiet repository."""
    _sidecar(tmp_path)
    _record(tmp_path, baseline=1_000)
    report = load_report(tmp_path)
    assert report is not None
    assert report.saved_input_tokens == 1_000


def test_a_corrupt_sidecar_degrades_instead_of_raising(tmp_path: Path) -> None:
    """This runs inside a dashboard endpoint and a CLI command; neither may die
    because a sidecar was truncated mid-write."""
    database = tmp_path / ".repowise" / "omissions" / "omissions.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"this is not a sqlite database")
    assert load_report(tmp_path) is None


def test_the_service_does_not_write_to_the_sidecar(tmp_path: Path) -> None:
    """Reporting must never upgrade a schema or create a store as a side effect."""
    _sidecar(tmp_path)
    _record(tmp_path, baseline=500)
    database = tmp_path / ".repowise" / "omissions" / "omissions.db"
    before = database.stat().st_mtime_ns, database.read_bytes()

    load_report(tmp_path)

    assert (database.stat().st_mtime_ns, database.read_bytes()) == before


def test_the_sql_report_and_the_pure_report_agree(tmp_path: Path) -> None:
    """Two report builders exist, and they are only useful while identical.

    The pure one is the hand-checkable oracle; the SQL one is what ships. A
    breakdown added to one and not the other is the drift this catches.
    """
    import sqlite3
    from dataclasses import asdict

    _sidecar(tmp_path)
    _record(tmp_path, baseline=1_000, surface="mcp", agent="claude_code")
    _record(tmp_path, baseline=250, surface="distill", agent="codex")
    _record(tmp_path, baseline=90, surface="hook", agent="claude_code")

    as_of = datetime.now(UTC)
    from_sql = load_report(tmp_path, as_of=as_of)
    assert from_sql is not None

    database = tmp_path / ".repowise" / "omissions" / "omissions.db"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM savings_events").fetchall()
    connection.close()

    from repowise.core.savings.contracts import SavingsEvent

    events = [
        SavingsEvent.from_mapping(
            {**dict(row), "is_usable": bool(row["is_usable"])}, accept_event_id=True
        )
        for row in rows
    ]
    from_pure = build_report(events, (), as_of=as_of)

    assert asdict(from_sql) == asdict(from_pure)

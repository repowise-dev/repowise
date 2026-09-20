"""The one report service every first-party savings surface reads through."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.core.savings import recorder
from repowise.core.savings.correlation import new_event_id, scoped_idempotency_key
from repowise.core.savings.reporting import build_report
from repowise.core.savings.service import load_report


def _sidecar(repo: Path) -> None:
    OmissionStore(repo / ".repowise" / "omissions" / "omissions.db").close()


def _record(
    repo: Path,
    *,
    baseline: int,
    surface: str = "mcp",
    agent: str = "codex",
    operation: str = "get_answer",
    evidence_kind: str = "measured",
    occurred_at: datetime | str | None = None,
    priced: bool = False,
) -> None:
    event_id = new_event_id()
    payload: dict = {
        "event_id": event_id,
        "idempotency_key": scoped_idempotency_key(str(repo), surface, event_id),
        "occurred_at": occurred_at or datetime.now(UTC),
        "surface": surface,
        "integration": agent,
        "agent": agent,
        "operation": operation,
        "evidence_kind": evidence_kind,
        "estimator": "chars_per_token_floor_v1",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": baseline,
        "pre_budget_input_tokens": baseline,
        "delivered_input_tokens": 0,
    }
    if priced:
        payload.update(
            model="claude-opus-5",
            currency="USD",
            pricing_source="session_model:claude_code",
            pricing_version="pricing:test",
            input_rate_usd_per_million=5.0,
            output_rate_usd_per_million=25.0,
        )
    assert recorder.record_event(repo, payload)


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


def test_an_unnormalized_path_still_finds_its_own_events(tmp_path: Path) -> None:
    """The reader and the writer must agree on the id, whatever spelling they get.

    The server stores ``local_path`` verbatim from the request that registered
    the repository, so it can carry a trailing separator or forward slashes on
    Windows. ``sidecar_path`` normalizes through Path and finds the database
    either way, so a mismatch here does not error -- it reports ``available``
    with a confident zero, which is indistinguishable from a quiet repository.
    """
    _sidecar(tmp_path)
    _record(tmp_path, baseline=1_000)

    for spelling in (f"{tmp_path}{os.sep}", str(tmp_path).replace(os.sep, "/")):
        report = load_report(spelling)
        assert report is not None, spelling
        assert report.saved_input_tokens == 1_000, spelling


def test_an_enormous_window_reports_nothing_rather_than_raising(tmp_path: Path) -> None:
    """``days`` large enough to overflow a timedelta is still a read path.

    OverflowError is not a ValueError, so it escaped the guard and surfaced as
    a 500 from the savings endpoint.
    """
    _sidecar(tmp_path)
    _record(tmp_path, baseline=1_000)
    assert load_report(tmp_path, days=999_999_999) is None
    assert load_report(tmp_path, days=10**12) is None


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
    # Deliberately the cases a three-identical-event fixture would not reach:
    # priced beside unpriced (so the null-model group exists and the USD sums
    # are non-zero), a tie on saved tokens across a null and a non-null group
    # (the exact ordering the two builders have to agree on), both evidence
    # kinds, and a multi-day series.
    _record(tmp_path, baseline=1_000, surface="mcp", agent="claude_code", priced=True)
    _record(tmp_path, baseline=1_000, surface="distill", agent="codex", priced=False)
    _record(
        tmp_path,
        baseline=250,
        surface="hook",
        agent="claude_code",
        operation="read_skeleton",
        evidence_kind="inferred",
        occurred_at="2026-09-10T08:00:00.000000Z",
    )
    _record(
        tmp_path,
        baseline=90,
        surface="mcp",
        agent="unknown",
        occurred_at="2026-09-11T08:00:00.000000Z",
        priced=True,
    )
    # A measured MCP event whose pre-budget and baseline differ. The reduction
    # denominator is the one case where the two builders express the same rule
    # in two languages -- a Python branch and a SQL CASE -- and without this
    # event they would agree by accident on every fixture here.
    _record_raw(
        tmp_path,
        surface="mcp",
        evidence_kind="measured",
        baseline_input_tokens=400,
        pre_budget_input_tokens=30_000,
        delivered_input_tokens=2_000,
    )

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

    sql, pure = asdict(from_sql), asdict(from_pure)
    # Floats compared with a tolerance: SQLite sums the priced tokens in table
    # order and the pure builder in list order, and float addition is not
    # associative, so exact equality here would be a flake waiting to happen.
    for key in ("priced_input_savings_usd", "priced_output_savings_usd"):
        assert sql.pop(key) == pytest.approx(pure.pop(key))
    assert sql == pure

    # Guard rails on the fixture itself: if it ever stops covering the mixed
    # cases, the equality above stops being worth much.
    assert from_sql.priced_input_savings_usd > 0
    assert from_sql.unpriced_saved_input_tokens > 0
    assert len(from_sql.per_day) == 3
    assert any(row["model"] is None for row in from_sql.per_model)
    assert from_sql.inferred_saved_input_tokens > 0
    # The reduction fields are only compared above while the fixture actually
    # carries baselines. The percentile in particular is a rank, so a builder
    # that was off by one would agree on a one-event population by accident.
    assert from_sql.baseline_events == 5
    assert from_sql.input_reduction_ratio is not None
    assert from_sql.input_reduction_ratio_p90 is not None


def test_a_measured_mcp_event_divides_by_what_its_saving_was_computed_against(
    tmp_path: Path,
) -> None:
    """The decision table gives a measured MCP event its pre-budget size as the
    formula baseline, not ``baseline_input_tokens``. Dividing by the wrong one
    reports a reduction over 100%: a real ledger read 424% before this.
    """
    _sidecar(tmp_path)
    _record_raw(
        tmp_path,
        surface="mcp",
        evidence_kind="measured",
        # An estimator floor, an order of magnitude under what was really shed.
        baseline_input_tokens=400,
        pre_budget_input_tokens=10_000,
        delivered_input_tokens=1_000,
    )
    report = load_report(tmp_path)
    assert report is not None
    assert report.saved_input_tokens == 9_000
    assert report.baseline_input_tokens == 10_000
    assert report.input_reduction_ratio == pytest.approx(0.9)


def test_no_window_can_report_a_reduction_over_one(tmp_path: Path) -> None:
    """``saved`` is ``clamp(denominator - delivered)``, so the ratio it forms
    is in [0, 1] on every event whatever the surface. Asserted rather than
    reasoned about, across the mix of surfaces that made it false once.
    """
    _sidecar(tmp_path)
    _record_raw(
        tmp_path,
        surface="mcp",
        evidence_kind="measured",
        baseline_input_tokens=400,
        pre_budget_input_tokens=50_000,
        delivered_input_tokens=500,
    )
    _record_raw(
        tmp_path,
        surface="mcp",
        evidence_kind="inferred",
        baseline_input_tokens=8_000,
        pre_budget_input_tokens=2_000,
        delivered_input_tokens=1_500,
    )
    _record_raw(
        tmp_path,
        surface="distill",
        evidence_kind="measured",
        baseline_input_tokens=6_000,
        pre_budget_input_tokens=6_000,
        delivered_input_tokens=600,
    )
    report = load_report(tmp_path)
    assert report is not None
    assert report.input_reduction_ratio is not None
    assert 0.0 <= report.input_reduction_ratio <= 1.0
    assert report.input_reduction_ratio_p90 is not None
    assert 0.0 <= report.input_reduction_ratio_p90 <= 1.0
    assert report.baseline_saved_input_tokens <= report.baseline_input_tokens


def _record_raw(
    repo: Path,
    *,
    surface: str,
    evidence_kind: str,
    baseline_input_tokens: int,
    pre_budget_input_tokens: int,
    delivered_input_tokens: int,
) -> None:
    """Record one event with every token dimension set independently.

    ``_record`` ties baseline and pre-budget together, which is exactly the
    case these two tests must not use.
    """
    event_id = new_event_id()
    recorder.record_event(
        repo,
        {
            "event_id": event_id,
            "idempotency_key": scoped_idempotency_key(str(repo), surface, event_id),
            "occurred_at": datetime.now(UTC),
            "surface": surface,
            "integration": "codex",
            "agent": "codex",
            "operation": "get_answer",
            "evidence_kind": evidence_kind,
            "estimator": "chars_per_token_floor_v1",
            "token_unit": "estimated_tokens",
            "result_state": "success",
            "is_usable": True,
            "baseline_input_tokens": baseline_input_tokens,
            "pre_budget_input_tokens": pre_budget_input_tokens,
            "delivered_input_tokens": delivered_input_tokens,
        },
    )

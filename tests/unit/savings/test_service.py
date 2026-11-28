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
    session_id: str | None = None,
    subagent_id: str | None = None,
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
        "session_id": session_id,
        "subagent_id": subagent_id,
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
    # A session with a captured rate and an unpriced event beside it, which the
    # report backfills, and an event recorded inside a subagent.
    _record(tmp_path, baseline=300, agent="claude_code", session_id="s-1", priced=True)
    _record(tmp_path, baseline=200, agent="claude_code", session_id="s-1")
    _record(tmp_path, baseline=150, agent="codex", subagent_id="agent-1")

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
    for key in (
        "priced_input_savings_usd",
        "priced_output_savings_usd",
        "backfilled_input_savings_usd",
    ):
        assert sql.pop(key) == pytest.approx(pure.pop(key))
    for key in ("per_operation", "per_surface", "per_model", "per_day"):
        sql_rows = [dict(row) for row in sql.pop(key)]
        pure_rows = [dict(row) for row in pure.pop(key)]
        assert [row.pop("priced_input_savings_usd") for row in sql_rows] == pytest.approx(
            [row.pop("priced_input_savings_usd") for row in pure_rows]
        )
        assert sql_rows == pure_rows
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
    assert from_sql.baseline_events == 8
    assert from_sql.reducing_events == 8
    assert from_sql.input_reduction_ratio is not None
    assert from_sql.input_reduction_ratio_p90 is not None
    # Only the session's own event is backfilled: no pricing cache here.
    assert from_sql.backfilled_saved_input_tokens == 200
    assert from_sql.backfilled_input_savings_usd == pytest.approx(200 * 5.0 / 1_000_000)
    assert (from_sql.subagent_events, from_sql.subagent_saved_input_tokens) == (1, 150)


def test_the_backfill_falls_back_to_the_agents_rate_in_both_builders(tmp_path: Path) -> None:
    """An unpriced event outside any priced session takes its agent's cached rate.

    Both builders take the same callable, so this compares them on the branch
    the parity fixture above cannot reach without a pricing cache.
    """
    import sqlite3

    from repowise.core.savings.contracts import SavingsEvent
    from repowise.core.savings.repository import SavingsRepository

    _sidecar(tmp_path)
    _record(tmp_path, baseline=1_000, agent="codex")
    _record(tmp_path, baseline=400, agent="unknown")
    _record(tmp_path, baseline=300, agent="claude_code", session_id="s-1", priced=True)
    _record(tmp_path, baseline=100, agent="claude_code", session_id="s-1")
    rates = {"codex": 1.25}

    database = tmp_path / ".repowise" / "omissions" / "omissions.db"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    as_of = datetime.now(UTC)
    from_sql = SavingsRepository(connection).report(
        str(tmp_path), as_of=as_of, fallback_rate=rates.get
    )
    rows = connection.execute("SELECT * FROM savings_events").fetchall()
    connection.close()
    events = [
        SavingsEvent.from_mapping(
            {**dict(row), "is_usable": bool(row["is_usable"])}, accept_event_id=True
        )
        for row in rows
    ]
    from_pure = build_report(events, as_of=as_of, fallback_rate=rates.get)

    for report in (from_sql, from_pure):
        # codex at its cached rate, the session event at its session's rate,
        # and the unknown agent, with no rate anywhere, left unpriced.
        assert report.backfilled_saved_input_tokens == 1_100
        assert report.backfilled_input_savings_usd == pytest.approx(
            (1_000 * 1.25 + 100 * 5.0) / 1_000_000
        )
        assert report.unpriced_saved_input_tokens == 1_500
        assert report.priced_input_savings_usd == pytest.approx(300 * 5.0 / 1_000_000)


def test_a_failing_backfill_never_costs_the_report(tmp_path: Path) -> None:
    """The backfill is a refinement on top of the report, never a reason to lose it."""
    import sqlite3

    from repowise.core.savings.repository import SavingsRepository

    _sidecar(tmp_path)
    _record(tmp_path, baseline=1_000)

    def boom(agent: str) -> float | None:
        raise RuntimeError("pricing exploded")

    connection = sqlite3.connect(tmp_path / ".repowise" / "omissions" / "omissions.db")
    try:
        report = SavingsRepository(connection).report(
            str(tmp_path), as_of=datetime.now(UTC), fallback_rate=boom
        )
    finally:
        connection.close()
    assert report.saved_input_tokens == 1_000
    assert report.backfilled_saved_input_tokens == 0


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


def test_an_interaction_that_saved_nothing_is_counted_but_not_averaged_in(
    tmp_path: Path,
) -> None:
    """The ratio answers "when it fires, by how much", so a zero-saving
    interaction must not drag it down -- and must still be visible, or the
    ratio is a selection with the selection hidden."""
    _sidecar(tmp_path)
    _record_raw(
        tmp_path,
        surface="distill",
        evidence_kind="measured",
        baseline_input_tokens=1_000,
        pre_budget_input_tokens=1_000,
        delivered_input_tokens=200,
    )
    _record_raw(
        tmp_path,
        surface="distill",
        evidence_kind="measured",
        baseline_input_tokens=1_000,
        pre_budget_input_tokens=1_000,
        delivered_input_tokens=1_000,
    )
    report = load_report(tmp_path)
    assert report is not None
    assert report.baseline_events == 2
    assert report.reducing_events == 1
    # 800/1000, not 800/2000: the second interaction is coverage, not a zero.
    assert report.input_reduction_ratio == pytest.approx(0.8)
    assert report.baseline_input_tokens == 1_000


def test_agent_calls_give_each_agent_its_first_and_last_mcp_call(tmp_path: Path) -> None:
    from repowise.core.savings.service import load_agent_calls

    assert load_agent_calls(tmp_path) is None
    _sidecar(tmp_path)
    assert load_agent_calls(tmp_path) == {}
    _record(tmp_path, baseline=10, agent="codex", occurred_at="2026-10-01T10:00:00Z")
    _record(tmp_path, baseline=10, agent="codex", occurred_at="2026-10-03T10:00:00Z")
    _record(tmp_path, baseline=10, agent="claude_code", occurred_at="2026-10-02T10:00:00Z")
    # A distill event is not a call the agent made to the index.
    _record(
        tmp_path, baseline=10, agent="cursor", surface="distill", occurred_at="2026-10-04T10:00:00Z"
    )
    calls = load_agent_calls(tmp_path)
    assert set(calls) == {"codex", "claude_code"}
    assert calls["codex"][0].startswith("2026-10-01")
    assert calls["codex"][1].startswith("2026-10-03")


def test_agent_usage_counts_each_agent_s_calls(tmp_path: Path) -> None:
    from repowise.core.savings.service import load_agent_usage

    assert load_agent_usage(tmp_path) is None
    _sidecar(tmp_path)
    for day in (1, 2, 3):
        _record(tmp_path, baseline=10, agent="claude_code", occurred_at=f"2026-10-0{day}T10:00:00Z")
    _record(tmp_path, baseline=10, agent="codex", occurred_at="2026-10-05T10:00:00Z")
    _record(tmp_path, baseline=10, agent="claude_code", surface="distill")
    usage = load_agent_usage(tmp_path)
    assert usage["claude_code"].calls == 3 and usage["codex"].calls == 1
    assert usage["claude_code"].first.startswith("2026-10-01")


@pytest.mark.parametrize(
    ("source", "backfilled"),
    [("session_model:claude_code", 1_000), ("session_model:unknown", 0)],
)
def test_the_report_backfills_from_the_cached_rate_but_never_the_default(
    tmp_path: Path, source: str, backfilled: int
) -> None:
    """A sighted model is evidence; the default used when nothing was sighted is not."""
    from repowise.core.savings import pricing

    _sidecar(tmp_path)
    _record(tmp_path, baseline=1_000, agent="claude_code")
    snapshot = pricing.PricingSnapshot(
        model="claude-sonnet-4-6",
        pricing_source=source,
        pricing_version="pricing:test",
        input_rate_usd_per_million=3.0,
        output_rate_usd_per_million=15.0,
    )
    pricing.clear_pricing_cache()
    try:
        pricing._write_cache(
            pricing._cache_path(tmp_path), pricing._Snapshots(newest=snapshot, agents={})
        )
        report = load_report(tmp_path)
    finally:
        pricing.clear_pricing_cache()
    assert report is not None
    assert report.unpriced_saved_input_tokens == 1_000
    assert report.backfilled_saved_input_tokens == backfilled
    assert report.backfilled_input_savings_usd == pytest.approx(backfilled * 3.0 / 1_000_000)


def _record_backfill(
    repo: Path,
    *,
    harness: str,
    baseline: int,
    delivered: int,
    estimator: str = "transcript_marker_v1",
) -> None:
    """One transcript-backfill event, with its token fields stored as given."""
    event_id = new_event_id()
    assert recorder.record_event(
        repo,
        {
            "event_id": event_id,
            "idempotency_key": scoped_idempotency_key(str(repo), "distill", event_id),
            "occurred_at": datetime.now(UTC),
            "surface": "distill",
            "integration": harness,
            "agent": harness,
            "operation": "distill",
            "evidence_kind": "measured",
            "estimator": estimator,
            "token_unit": "estimated_tokens",
            "result_state": "success",
            "is_usable": True,
            "baseline_input_tokens": baseline,
            "pre_budget_input_tokens": baseline,
            "delivered_input_tokens": delivered,
        },
    )


def _record_old_codex_ledger(repo: Path) -> int:
    """A ledger with Codex backfill stored above the cap; returns its capped saving."""
    _sidecar(repo)
    # Over the cap: reported at the 10,025-token plateau.
    _record_backfill(repo, harness="codex", baseline=638_062, delivered=500)
    # Under the cap: untouched.
    _record_backfill(repo, harness="codex", baseline=5_000, delivered=100)
    # Delivered more than the cap: the cap never clips below the delivery.
    _record_backfill(repo, harness="codex", baseline=30_000, delivered=20_000)
    # Not Codex backfill: another harness, and Codex's live distill.
    _record_backfill(repo, harness="claude_code", baseline=50_000, delivered=0)
    _record_backfill(
        repo, harness="codex", baseline=60_000, delivered=0, estimator="chars_per_token_floor_v1"
    )
    return (10_025 - 500) + 4_900 + 0 + 50_000 + 60_000


def test_codex_backfill_recorded_under_the_old_cap_reports_at_the_plateau(
    tmp_path: Path,
) -> None:
    """An existing ledger shows what a fresh backfill would record, without a rewrite."""
    expected = _record_old_codex_ledger(tmp_path)

    report = load_report(tmp_path)

    assert report is not None
    assert report.saved_input_tokens == expected
    assert report.unique_events == 5
    # Every aggregate reads the capped rows, not only the headline.
    assert sum(row["saved_input_tokens"] for row in report.per_agent) == expected
    for rows in (report.per_operation, report.per_day, report.per_model, report.per_surface):
        assert sum(row["saved_input_tokens"] for row in rows) == expected
        assert sum(row["priced_input_savings_usd"] for row in rows) == pytest.approx(
            report.priced_input_savings_usd
        )
    # The reduction denominator is capped with the saving, so the ratio agrees.
    assert report.baseline_input_tokens == 10_025 + 5_000 + 50_000 + 60_000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        # Breaks the SQL: a sqlite3 error inside the cap.
        ("TRANSCRIPT_ESTIMATOR", "x'"),
        # Breaks before any SQL: a plain Python error inside the cap.
        ("CODEX_OUTPUT_CAP_TOKENS", None),
    ],
)
def test_a_failing_codex_cap_never_costs_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: object
) -> None:
    """The cap is a correction on top of the report; failing reads the rows as written."""
    from repowise.core.savings import service

    _record_old_codex_ledger(tmp_path)
    monkeypatch.setattr(service, name, value)

    report = load_report(tmp_path)

    assert report is not None
    assert report.saved_input_tokens == (638_062 - 500) + 4_900 + 10_000 + 50_000 + 60_000

"""Check-tool outcomes: the contract, the in-place column upgrade and the report rows."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repowise.core.savings import schema
from repowise.core.savings.contracts import SavingsEvent, normalize_outcome
from repowise.core.savings.correlation import scoped_idempotency_key
from repowise.core.savings.reporting import CHECK_TOOLS, build_report
from repowise.core.savings.repository import SavingsRepository
from repowise.core.sqlite_pragmas import apply_sqlite_pragmas

AS_OF = datetime(2026, 11, 20, tzinfo=UTC)


def _event(
    index: int,
    operation: str,
    outcome: object = None,
    *,
    days_ago: float = 0,
    surface: str = "mcp",
    result_state: str = "success",
) -> SavingsEvent:
    when = (AS_OF - timedelta(days=days_ago, seconds=index + 1)).isoformat()
    payload: dict = {
        "idempotency_key": scoped_idempotency_key("repo", surface, index),
        "occurred_at": when.replace("+00:00", "Z"),
        "repository_id": "repo",
        "surface": surface,
        "integration": "claude_code",
        "agent": "claude_code",
        "operation": operation,
        "evidence_kind": "measured",
        "estimator": "fixture_v1",
        "token_unit": "estimated_tokens",
        "result_state": result_state,
        "is_usable": result_state == "success",
        "baseline_input_tokens": 100,
        "pre_budget_input_tokens": 100,
        "delivered_input_tokens": 40,
    }
    if outcome is not None:
        payload["outcome"] = outcome
    return SavingsEvent.from_mapping(payload)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    apply_sqlite_pragmas(conn, 1000)
    return conn


def _rows(report) -> dict[str, dict]:
    return {row["tool"]: dict(row) for row in report.checks}


def test_the_outcome_vocabulary_is_closed_and_bounded() -> None:
    assert normalize_outcome(None) is None
    assert normalize_outcome("high") is None
    assert normalize_outcome({}) == {}
    assert normalize_outcome(
        {"level": "high", "decision": "maybe", "files": -1, "items": True, "query": "x"}
    ) == {"level": "high"}
    assert normalize_outcome({"findings": 10**9}) == {"findings": 1_000_000}


def test_a_bad_outcome_costs_only_the_outcome() -> None:
    event = _event(1, "get_risk", {"high": "lots", "level": 3})
    assert event.outcome == {}
    assert _event(2, "get_risk", ["not", "a", "map"]).outcome is None


def _pre_outcome_sidecar(path: Path) -> None:
    """A v2 sidecar as shipped before outcomes, holding one event."""
    conn = _connect(path)
    ddl = schema._SCHEMA_TABLES[0]
    old = ddl.replace(
        "        outcome_json TEXT CHECK(outcome_json IS NULL OR length(outcome_json) <= 256),\n",
        "",
    )
    assert old != ddl
    for statement in schema._PRESERVED_TABLES:
        conn.execute(statement)
    conn.execute(schema._LEGACY_SAVINGS_TABLE)
    conn.execute(old)
    for statement in schema._SCHEMA_TABLES[1:]:
        conn.execute(statement)
    conn.execute(f"PRAGMA user_version={schema.SAVINGS_SCHEMA_VERSION}")
    conn.execute(
        "INSERT INTO savings_events (event_id, schema_version, idempotency_key, occurred_at, "
        "repository_id, surface, integration, agent, operation, evidence_kind, estimator, "
        "token_unit, result_state, is_usable, saved_input_tokens, metadata_json) VALUES "
        "('e1', 1, 'k1', '2026-11-19T00:00:00Z', 'repo', 'mcp', 'cursor', 'cursor', "
        "'get_why', 'measured', 'v', 'estimated_tokens', 'success', 1, 0, '{}')"
    )
    conn.close()


def test_an_older_sidecar_gains_the_column_in_place_once(tmp_path: Path) -> None:
    db = tmp_path / "omissions.db"
    _pre_outcome_sidecar(db)
    conn = _connect(db)
    schema.initialize_savings_schema(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(savings_events)")}
    assert "outcome_json" in columns
    assert conn.execute("SELECT event_id, outcome_json FROM savings_events").fetchall() == [
        ("e1", None)
    ]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == schema.SAVINGS_SCHEMA_VERSION
    # Idempotent: a second open finds nothing to do.
    schema.initialize_savings_schema(conn)
    assert len([r for r in conn.execute("PRAGMA table_info(savings_events)")]) == len(columns)

    repository = SavingsRepository(conn)
    assert repository.record_event(_event(1, "get_why", {"decision": "accepted"})) is True
    rows = _rows(repository.report("repo", as_of=AS_OF))
    # The old call counts; only the new one carries an outcome.
    assert rows["get_why"]["calls"] == 2
    assert rows["get_why"]["outcome_calls"] == 1
    assert rows["get_why"]["decisions_accepted"] == 1
    conn.close()


def test_the_column_rejects_an_oversized_outcome(tmp_path: Path) -> None:
    conn = _connect(tmp_path / "omissions.db")
    schema.initialize_savings_schema(conn)
    SavingsRepository(conn).record_event(_event(1, "get_health", {"findings": 1}))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE savings_events SET outcome_json = ?", ("x" * 257,))
    conn.close()


def _seeded(tmp_path: Path) -> tuple[SavingsRepository, list[SavingsEvent]]:
    events = [
        _event(1, "get_change_risk", {"level": "high"}),
        _event(2, "get_change_risk", {"level": "low"}),
        _event(3, "get_change_risk", {}),  # ranked nothing
        _event(4, "get_change_risk"),  # recorded before outcomes
        _event(5, "get_risk", {"high": 1, "medium": 2, "low": 3}),
        _event(6, "get_risk", {"high": 0, "medium": 1, "low": 0}),
        _event(7, "get_why", {"decision": "accepted"}),
        _event(8, "get_why", {"decision": "none"}),
        _event(9, "get_why", {"decision": "candidate"}),
        _event(10, "get_health", {"findings": 12, "files": 3}),
        _event(11, "get_health", {"findings": 0, "files": 1}),
        _event(12, "get_answer"),
        _event(13, "get_risk", {"high": 9}, result_state="error"),  # not a usable check
        _event(14, "get_why", {"decision": "accepted"}, surface="distill"),
        _event(15, "get_health", {"findings": 50, "files": 50}, days_ago=10),
    ]
    conn = _connect(tmp_path / "omissions.db")
    schema.initialize_savings_schema(conn)
    repository = SavingsRepository(conn)
    for event in events:
        assert repository.record_event(event) is True
    return repository, events


def test_check_rows_list_every_tool_with_their_outcomes(tmp_path: Path) -> None:
    repository, _ = _seeded(tmp_path)
    report = repository.report("repo", as_of=AS_OF, days=7)
    assert [row["tool"] for row in report.checks] == list(CHECK_TOOLS)
    rows = _rows(report)
    change = rows["get_change_risk"]
    assert (change["calls"], change["outcome_calls"]) == (4, 3)
    assert (change["high"], change["medium"], change["low"]) == (1, 0, 1)
    assert change["findings"] is None and change["decisions_accepted"] is None
    risk = rows["get_risk"]
    assert (risk["calls"], risk["high"], risk["medium"], risk["low"]) == (2, 1, 3, 3)
    why = rows["get_why"]
    assert (why["decisions_accepted"], why["decisions_candidate"], why["decisions_none"]) == (
        1,
        1,
        1,
    )
    health = rows["get_health"]
    assert (health["calls"], health["findings"], health["files"]) == (2, 12, 4)
    dead = rows["get_dead_code"]
    assert dead["calls"] == 0 and dead["outcome_calls"] == 0
    assert dead["items"] is None and dead["high_confidence"] is None


def test_the_window_bounds_check_rows(tmp_path: Path) -> None:
    repository, _ = _seeded(tmp_path)
    rows = _rows(repository.report("repo", as_of=AS_OF))
    assert (rows["get_health"]["calls"], rows["get_health"]["findings"]) == (3, 62)


def test_calls_without_outcomes_report_null_outcomes(tmp_path: Path) -> None:
    conn = _connect(tmp_path / "omissions.db")
    schema.initialize_savings_schema(conn)
    repository = SavingsRepository(conn)
    repository.record_event(_event(1, "get_dead_code"))
    row = _rows(repository.report("repo", as_of=AS_OF))["get_dead_code"]
    assert (row["calls"], row["outcome_calls"], row["items"]) == (1, 0, None)


def test_the_pure_report_and_the_ledger_agree_on_check_rows(tmp_path: Path) -> None:
    repository, events = _seeded(tmp_path)
    for days in (None, 7):
        ledger = repository.report("repo", as_of=AS_OF, days=days).checks
        pure = build_report(events, as_of=AS_OF, days=days).checks
        assert ledger == pure


def test_a_sidecar_read_before_its_upgrade_reports_calls_without_outcomes(tmp_path: Path) -> None:
    db = tmp_path / "omissions.db"
    _pre_outcome_sidecar(db)
    conn = _connect(db)
    row = _rows(SavingsRepository(conn).report("repo", as_of=AS_OF))["get_why"]
    assert (row["calls"], row["outcome_calls"], row["decisions_accepted"]) == (1, 0, None)
    conn.close()


def test_outcome_sums_stay_exact_past_one_page_of_distinct_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.savings import repository as repository_module

    monkeypatch.setattr(repository_module, "_CHECK_OUTCOME_PAGE", 3)
    # 11 distinct health outcomes and 4 distinct risk ones: several pages, and
    # a page boundary that falls between two tools.
    events = [_event(i, "get_health", {"findings": i, "files": 1}) for i in range(11)]
    events += [_event(100 + i, "get_risk", {"high": i, "low": 1}) for i in range(4)]
    events.append(_event(200, "get_health", {"findings": 3, "files": 1}))  # a repeated outcome
    conn = _connect(tmp_path / "omissions.db")
    schema.initialize_savings_schema(conn)
    repository = SavingsRepository(conn)
    for event in events:
        repository.record_event(event)
    report = repository.report("repo", as_of=AS_OF)
    rows = _rows(report)
    health, risk = rows["get_health"], rows["get_risk"]
    assert (health["calls"], health["outcome_calls"]) == (12, 12)
    assert (health["findings"], health["files"]) == (sum(range(11)) + 3, 12)
    assert (risk["calls"], risk["outcome_calls"], risk["high"], risk["low"]) == (4, 4, 6, 4)
    assert report.checks == build_report(events, as_of=AS_OF).checks
    conn.close()

"""Output tokens saved, estimated per question from the paired benchmark."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.core.savings import output_estimate, recorder
from repowise.core.savings.contracts import SavingsEvent
from repowise.core.savings.correlation import new_event_id, scoped_idempotency_key
from repowise.core.savings.output_estimate import (
    ANTHROPIC_FALLBACK,
    MEASURED,
    OTHER_FALLBACK,
    QUESTIONS_PER_SESSION_CAP,
    _Calibration,
    estimate_output,
)
from repowise.core.savings.reporting import build_report
from repowise.core.savings.service import load_report

#: Hand-checkable rows: reductions 50%, 10%, 20%, 5%; bare median 1,600;
#: calls per question 4, 2, 3, 8, so the median divisor is 3.5.
_ROWS = {
    "claude-sonnet-4-6": (2_000.0, 1_000.0, 4.0),
    "claude-opus-5-5": (1_000.0, 900.0, 2.0),
    "claude-opus-4-8": (1_200.0, 960.0, 3.0),
    "claude-haiku-4-5": (3_000.0, 2_850.0, 8.0),
}


@pytest.fixture()
def fixed_calibration(monkeypatch) -> None:
    monkeypatch.setattr(
        output_estimate, "_load_calibration", lambda: _Calibration("bench", "v-test", _ROWS)
    )


def _by_model(estimate) -> dict:
    return {(row.model, row.output_rate_usd_per_million): row for row in estimate.per_model}


def test_a_measured_model_is_credited_its_own_difference_per_question(
    fixed_calibration,
) -> None:
    estimate = estimate_output(
        [
            # 12 calls at 4 per question: 3 questions in each of 3 sessions.
            ("claude-sonnet-4-6", 15.0, 12, 3),
            # One call still counts as one question.
            ("claude-sonnet-4-6", 15.0, 1, 1),
            # 400 calls would be 100 questions; the cap holds it at 20.
            ("claude-sonnet-4-6", 15.0, 400, 1),
        ]
    )
    row = estimate.per_model[0]
    assert (row.basis, row.output_tokens_per_question, row.mcp_calls_per_question) == (
        MEASURED,
        1_000,
        4.0,
    )
    assert QUESTIONS_PER_SESSION_CAP == 20
    assert (row.sessions, row.questions) == (5, 9 + 1 + 20)
    assert estimate.output_tokens_saved == 30_000
    assert estimate.output_usd_saved == pytest.approx(30_000 * 15.0 / 1_000_000)
    assert (estimate.source, estimate.version, estimate.sessions) == ("bench", "v-test", 5)
    block = estimate.calibration()
    assert block["per_question"] is True
    assert block["questions_per_session_cap"] == 20
    assert "output_tokens_saved" not in block


def test_fallbacks_never_use_the_best_row(fixed_calibration) -> None:
    estimate = estimate_output(
        [
            # Claude, no row, family measured: min(median 15%, opus min 10%).
            ("claude-opus-6", 25.0, 1, 1),
            # Claude, no row, no family row: the cross-model median, 15%.
            ("claude-fable-5-1", 25.0, 1, 1),
            # Not Anthropic: the lowest measured reduction, 5%; 7 calls over
            # the median divisor of 3.5 is 2 questions.
            ("gpt-5.6-sol", 10.0, 7, 1),
            # No model: same conservative rate, and no dollars.
            (None, None, 1, 2),
        ]
    )
    rows = _by_model(estimate)
    assert rows[("claude-opus-6", 25.0)].basis == ANTHROPIC_FALLBACK
    assert rows[("claude-opus-6", 25.0)].output_tokens_per_question == 160
    assert rows[("claude-fable-5-1", 25.0)].output_tokens_per_question == 240
    gpt = rows[("gpt-5.6-sol", 10.0)]
    assert (gpt.basis, gpt.output_tokens_per_question, gpt.mcp_calls_per_question) == (
        OTHER_FALLBACK,
        80,
        3.5,
    )
    assert gpt.output_tokens_saved == 160
    unknown = rows[(None, None)]
    assert (unknown.basis, unknown.output_tokens_saved, unknown.output_usd_saved) == (
        OTHER_FALLBACK,
        160,
        None,
    )
    assert estimate.unpriced_output_tokens_saved == 160
    assert estimate.output_tokens_saved == 160 + 240 + 160 + 160


def test_a_measured_increase_credits_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        output_estimate,
        "_load_calibration",
        lambda: _Calibration("bench", "v", {"claude-haiku-4-5": (1_000.0, 1_100.0, 5.0)}),
    )
    estimate = estimate_output([("claude-haiku-4-5", 5.0, 9, 4), ("gpt-x", 1.0, 3, 1)])
    assert estimate.output_tokens_saved == 0
    assert all(row.reduction == 0.0 for row in estimate.per_model)


def test_the_shipped_calibration_loads_and_holds_its_own_rows() -> None:
    output_estimate._load_calibration.cache_clear()
    payload = json.loads(output_estimate.CALIBRATION_PATH.read_text(encoding="utf-8"))
    estimate = estimate_output([(model, 1.0, 1, 1) for model in payload["models"]])
    assert estimate is not None
    assert estimate.version == payload["version"]
    for row in estimate.per_model:
        measured = payload["models"][row.model]
        assert row.basis == MEASURED
        assert row.mcp_calls_per_question == measured["repowise_mcp_calls_per_question"]
        assert row.output_tokens_per_question == round(
            measured["bare_output_tokens"] - measured["repowise_output_tokens"]
        )


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("gone"), ValueError("malformed"), KeyError("models")],
)
def test_a_broken_calibration_means_no_estimate_never_a_failed_report(
    tmp_path: Path, monkeypatch, failure: Exception
) -> None:
    _record(tmp_path, session="s1", model="claude-sonnet-4-6", rate=15.0)

    def boom():
        raise failure

    monkeypatch.setattr(output_estimate, "_load_calibration", boom)
    report = load_report(tmp_path)
    assert report is not None
    assert report.output_estimate is None
    assert report.saved_input_tokens == 1_000


# -- session selection, both builders ---------------------------------------


def _record(
    repo: Path,
    *,
    session: str | None,
    surface: str = "mcp",
    model: str | None = None,
    rate: float | None = None,
) -> None:
    OmissionStore(repo / ".repowise" / "omissions" / "omissions.db").close()
    event_id = new_event_id()
    payload: dict = {
        "event_id": event_id,
        "idempotency_key": scoped_idempotency_key(str(repo), surface, event_id),
        "occurred_at": datetime.now(UTC),
        "surface": surface,
        "integration": "claude_code",
        "agent": "claude_code",
        "session_id": session,
        "operation": "get_answer",
        "evidence_kind": "measured",
        "estimator": "chars_per_token_floor_v1",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": 1_000,
        "pre_budget_input_tokens": 1_000,
        "delivered_input_tokens": 0,
    }
    if model:
        payload.update(
            model=model,
            currency="USD",
            pricing_source="session_model:claude_code",
            pricing_version="pricing:test",
            input_rate_usd_per_million=3.0,
            output_rate_usd_per_million=rate,
        )
    assert recorder.record_event(repo, payload)


def test_each_session_that_used_repowise_counts_once_on_its_main_model(
    tmp_path: Path, fixed_calibration
) -> None:
    # One session, three MCP calls on Sonnet and one on Opus.
    for _ in range(3):
        _record(tmp_path, session="a", model="claude-sonnet-4-6", rate=15.0)
    _record(tmp_path, session="a", model="claude-opus-5-5", rate=25.0)
    # A hook-only session does not count: its id is hashed per surface, so it
    # cannot be told apart from an MCP session of the same conversation.
    _record(tmp_path, session="b", surface="hook", model="claude-opus-5-5", rate=25.0)
    # A distill-only session does not, nor does an event with no session.
    _record(tmp_path, session="c", surface="distill", model="claude-sonnet-4-6", rate=15.0)
    _record(tmp_path, session=None, model="claude-sonnet-4-6", rate=15.0)
    # A priced event outranks unpriced ones in the same session.
    _record(tmp_path, session="d")
    _record(tmp_path, session="d")
    _record(tmp_path, session="d", model="claude-opus-5-5", rate=25.0)

    report = load_report(tmp_path)
    estimate = report.output_estimate
    rows = _by_model(estimate)
    assert estimate.sessions == 2
    # Session a: 4 calls at 4 per question is one question on Sonnet.
    assert rows[("claude-sonnet-4-6", 15.0)].questions == 1
    # Session d: 3 calls at 2 per question is 1.5 questions on Opus.
    assert rows[("claude-opus-5-5", 25.0)].questions == 1.5
    assert estimate.output_tokens_saved == 1_000 + 150
    assert estimate.output_usd_saved == pytest.approx((1_000 * 15.0 + 150 * 25.0) / 1_000_000)


def test_sql_and_pure_builders_agree_on_the_estimate(tmp_path: Path, fixed_calibration) -> None:
    for session, surface, model, rate in (
        ("a", "mcp", "claude-sonnet-4-6", 15.0),
        ("a", "mcp", "claude-opus-5-5", 25.0),
        ("b", "mcp", None, None),
        ("e", "hook", "claude-opus-5-5", 25.0),
        ("c", "mcp", "gpt-5.6-sol", 10.0),
        ("c", "mcp", "gpt-5.6-sol", 10.0),
        ("d", "distill", "claude-opus-5-5", 25.0),
    ):
        _record(tmp_path, session=session, surface=surface, model=model, rate=rate)
    as_of = datetime.now(UTC)
    from_sql = load_report(tmp_path, as_of=as_of)

    database = tmp_path / ".repowise" / "omissions" / "omissions.db"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM savings_events").fetchall()
    connection.close()
    events = [
        SavingsEvent.from_mapping(
            {**dict(row), "is_usable": bool(row["is_usable"])}, accept_event_id=True
        )
        for row in rows
    ]
    from_pure = build_report(events, (), as_of=as_of)

    assert from_sql.output_estimate is not None
    assert from_sql.output_estimate.sessions == 3
    assert asdict(from_sql.output_estimate) == asdict(from_pure.output_estimate)


def test_a_context_window_tag_still_finds_the_model_s_own_row(fixed_calibration) -> None:
    estimate = estimate_output([("claude-sonnet-4-6[1m]", 15.0, 1, 1)])
    row = estimate.per_model[0]
    assert (row.basis, row.output_tokens_per_question) == (MEASURED, 1_000)


def test_a_dated_model_id_still_finds_the_model_s_own_row(fixed_calibration) -> None:
    estimate = estimate_output([("claude-sonnet-4-6-20260101", 15.0, 1, 1)])
    row = estimate.per_model[0]
    assert (row.basis, row.output_tokens_per_question) == (MEASURED, 1_000)

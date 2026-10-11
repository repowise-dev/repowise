"""Core savings normalization, formulas, pricing, and fixture reporting."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.savings.contracts import OpportunityObservation, SavingsEvent
from repowise.core.savings.correlation import (
    hash_correlation_evidence,
    new_event_id,
    scoped_idempotency_key,
)
from repowise.core.savings.formulas import calculate_token_accounting
from repowise.core.savings.normalization import normalize_mcp_identity
from repowise.core.savings.pricing import price_tokens
from repowise.core.savings.reporting import build_report

FIXTURE = Path(__file__).parents[2] / "fixtures" / "savings" / "mixed_agents_v1.json"


def _fixture_domain() -> tuple[dict, list[SavingsEvent], list[OpportunityObservation]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events: dict[str, SavingsEvent] = {}
    for record in payload["records"]:
        event = SavingsEvent.from_mapping(record, accept_event_id=True)
        events.setdefault(event.idempotency_key, event)
    opportunities = [
        OpportunityObservation.from_mapping(item, repository_id="fixture-repo")
        for item in payload["opportunities"]
    ]
    return payload, list(events.values()), opportunities


@pytest.mark.parametrize(("scope", "days"), [("all", None), ("30d", 30), ("7d", 7)])
def test_core_report_reproduces_the_hand_calculable_fixture(scope: str, days: int | None) -> None:
    payload, events, opportunities = _fixture_domain()
    report = build_report(
        events,
        opportunities,
        as_of=datetime.fromisoformat(payload["as_of"]),
        days=days,
    )
    actual = asdict(report)
    for key, expected in payload["expected"][scope].items():
        if isinstance(expected, float):
            assert actual[key] == pytest.approx(expected)
        else:
            assert actual[key] == expected


def test_same_tool_events_correlate_independently_and_zero_saving_counts() -> None:
    base = {
        "occurred_at": "2026-09-14T00:00:00Z",
        "repository_id": "repo",
        "surface": "mcp",
        "integration": "codex",
        "agent": "codex",
        "operation": "get_risk",
        "confidence": 1.0,
        "estimator": "fixture_v1",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_output_tokens": None,
        "delivered_output_tokens": None,
        "model": None,
        "currency": None,
        "pricing_source": None,
        "pricing_version": None,
        "input_rate_usd_per_million": None,
        "output_rate_usd_per_million": None,
        "omission_refs": [],
    }
    inferred = SavingsEvent.from_mapping(
        {
            **base,
            "idempotency_key": scoped_idempotency_key("repo", "mcp", "call-a"),
            "evidence_kind": "inferred",
            "baseline_input_tokens": 1000,
            "pre_budget_input_tokens": 500,
            "delivered_input_tokens": 200,
            "dropped_input_tokens": 300,
            "saved_input_tokens": 800,
            "saved_output_tokens": None,
        }
    )
    measured = SavingsEvent.from_mapping(
        {
            **base,
            "idempotency_key": scoped_idempotency_key("repo", "mcp", "call-b"),
            "evidence_kind": "measured",
            "baseline_input_tokens": 800,
            "pre_budget_input_tokens": 800,
            "delivered_input_tokens": 500,
            "dropped_input_tokens": 300,
            "saved_input_tokens": 300,
            "saved_output_tokens": None,
        }
    )
    zero = SavingsEvent.from_mapping(
        {
            **base,
            "idempotency_key": scoped_idempotency_key("repo", "mcp", "call-zero"),
            "evidence_kind": "inferred",
            "baseline_input_tokens": 200,
            "pre_budget_input_tokens": 350,
            "delivered_input_tokens": 250,
            "dropped_input_tokens": 100,
            "saved_input_tokens": 0,
            "saved_output_tokens": None,
        }
    )
    report = build_report([inferred, measured, zero], as_of=datetime.now(UTC))

    assert report.saved_input_tokens == 1100
    assert report.mcp_queries_answered == 3
    assert report.saving_interactions == 2


def test_dimensions_clamp_and_unusable_results_never_save() -> None:
    measured = calculate_token_accounting(
        surface="distill",
        evidence_kind="measured",
        result_state="success",
        is_usable=True,
        baseline_input_tokens=-10,
        pre_budget_input_tokens=-20,
        delivered_input_tokens=50,
        baseline_output_tokens=10,
        delivered_output_tokens=20,
    )
    assert measured.saved_input_tokens == measured.dropped_input_tokens == 0
    assert measured.saved_output_tokens == 0

    for state, usable in (
        ("dead_end", False),
        ("error", False),
        ("unknown", False),
        ("partial", False),
    ):
        result = calculate_token_accounting(
            surface="mcp",
            evidence_kind="measured",
            result_state=state,
            is_usable=usable,
            baseline_input_tokens=1000,
            pre_budget_input_tokens=1000,
            delivered_input_tokens=100,
        )
        assert result.dropped_input_tokens == 900
        assert result.saved_input_tokens == 0


def test_identity_metadata_correlation_and_pricing_are_bounded() -> None:
    assert normalize_mcp_identity("Claude-Code")[0] == "claude_code"
    identity, metadata = normalize_mcp_identity("Untrusted Client!" * 20)
    assert identity == "unknown"
    assert len(metadata["client_info_normalized"]) == 64
    correlation = hash_correlation_evidence("session", "raw-secret-request-id")
    assert correlation.startswith("sha256:") and len(correlation) == 71
    assert "raw-secret" not in correlation
    assert new_event_id() != new_event_id()
    assert price_tokens(1000, 2.5) == pytest.approx(0.0025)
    assert price_tokens(1000, None) is None
    for missing in (None, ""):
        with pytest.raises(ValueError, match="evidence"):
            scoped_idempotency_key("repo", "mcp", missing)
    for invalid_rate in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ValueError, match="finite and nonnegative"):
            price_tokens(1000, invalid_rate)

    payload, _, _ = _fixture_domain()
    bad = dict(payload["records"][0], metadata={"prompt": "do not persist"})
    with pytest.raises(ValueError, match="metadata keys"):
        SavingsEvent.from_mapping(bad, accept_event_id=True)
    bad_price = dict(payload["records"][0], input_rate_usd_per_million=float("nan"))
    with pytest.raises(ValueError, match="finite and nonnegative"):
        SavingsEvent.from_mapping(bad_price, accept_event_id=True)


def test_report_keeps_unobserved_output_savings_unknown() -> None:
    event = _fixture_domain()[1][0]
    report = build_report([event], as_of=datetime.now(UTC))

    assert event.saved_output_tokens is None
    assert report.saved_output_tokens is None
    assert report.priced_saved_output_tokens == 0
    assert report.unpriced_saved_output_tokens == 0

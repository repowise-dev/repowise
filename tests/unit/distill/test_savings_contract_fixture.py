"""Phase 0 invariants for the hand-calculable savings contract fixture."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

FIXTURE = Path(__file__).parents[2] / "fixtures" / "savings" / "mixed_agents_v1.json"


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _unique_events(payload: dict) -> list[dict]:
    events: dict[str, dict] = {}
    for record in payload["records"]:
        key = record["idempotency_key"]
        comparable = {k: v for k, v in record.items() if k != "attempt_id"}
        if key in events:
            prior = {k: v for k, v in events[key].items() if k != "attempt_id"}
            assert comparable == prior, f"retry {key} changed its accounting payload"
            continue
        events[key] = record
    return list(events.values())


def _scope(records: list[dict], as_of: datetime, days: int | None) -> list[dict]:
    if days is None:
        return records
    cutoff = as_of - timedelta(days=days)
    return [record for record in records if datetime.fromisoformat(record["occurred_at"]) >= cutoff]


def _is_answered(event: dict) -> bool:
    return event["result_state"] in {"success", "partial"} and event["is_usable"]


def _totals(events: list[dict]) -> dict:
    saved = sum(event["saved_input_tokens"] for event in events)
    measured = sum(
        event["saved_input_tokens"] for event in events if event["evidence_kind"] == "measured"
    )
    inferred = sum(
        event["saved_input_tokens"] for event in events if event["evidence_kind"] == "inferred"
    )
    priced = sum(
        event["saved_input_tokens"]
        for event in events
        if event["input_rate_usd_per_million"] is not None
    )
    input_usd = sum(
        event["saved_input_tokens"] * (event["input_rate_usd_per_million"] or 0) / 1_000_000
        for event in events
    )
    output_tokens = sum(event["saved_output_tokens"] or 0 for event in events)
    output_usd = sum(
        (event["saved_output_tokens"] or 0)
        * (event["output_rate_usd_per_million"] or 0)
        / 1_000_000
        for event in events
    )
    return {
        "unique_events": len(events),
        "successful_or_usable_partial_events": sum(_is_answered(event) for event in events),
        "saving_interactions": sum(event["saved_input_tokens"] > 0 for event in events),
        "mcp_queries_answered": sum(
            event["surface"] == "mcp" and _is_answered(event) for event in events
        ),
        "dead_ends": sum(event["result_state"] == "dead_end" for event in events),
        "saved_input_tokens": saved,
        "measured_saved_input_tokens": measured,
        "inferred_saved_input_tokens": inferred,
        "priced_saved_input_tokens": priced,
        "unpriced_saved_input_tokens": saved - priced,
        "priced_input_savings_usd": input_usd,
        "saved_output_tokens": output_tokens,
        "priced_output_savings_usd": output_usd,
    }


def test_fixture_has_deterministic_identity_and_formula_coverage() -> None:
    payload = _load()
    events = _unique_events(payload)

    assert len(payload["records"]) == 14
    assert len(events) == 13
    assert {event["agent"] for event in events} == {
        "claude_code",
        "codex",
        "cursor",
        "hermes",
        "opencode",
        "unknown",
        "vscode",
    }
    assert {event["surface"] for event in events} == {"distill", "hook", "mcp", "vscode_lm"}
    assert {event["result_state"] for event in events} == {
        "dead_end",
        "error",
        "partial",
        "success",
        "unknown",
    }
    assert any(event["subagent_id"] for event in events)
    assert any(event["model"] is None for event in events)
    assert any(event["saved_output_tokens"] is not None for event in events)

    required_keys = {
        "event_id",
        "idempotency_key",
        "occurred_at",
        "repository_id",
        "surface",
        "integration",
        "agent",
        "subagent_id",
        "session_id",
        "request_id",
        "tool_call_id",
        "operation",
        "evidence_kind",
        "confidence",
        "estimator",
        "token_unit",
        "result_state",
        "is_usable",
        "baseline_input_tokens",
        "pre_budget_input_tokens",
        "delivered_input_tokens",
        "dropped_input_tokens",
        "saved_input_tokens",
        "baseline_output_tokens",
        "delivered_output_tokens",
        "saved_output_tokens",
        "model",
        "currency",
        "pricing_source",
        "pricing_version",
        "input_rate_usd_per_million",
        "output_rate_usd_per_million",
        "omission_refs",
    }

    for event in events:
        allowed_keys = required_keys | {"attempt_id", "metadata"}
        assert set(event) <= allowed_keys
        UUID(event["event_id"])
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", event["idempotency_key"])
        assert event["token_unit"] == payload["token_unit"]["name"]
        assert event["confidence"] is None or 0 <= event["confidence"] <= 1
        if event["result_state"] == "success":
            assert event["is_usable"] is True
        if event["result_state"] in {"dead_end", "error", "unknown"}:
            assert event["is_usable"] is False
        assert event["saved_input_tokens"] >= 0
        if event["result_state"] in {"dead_end", "error", "unknown"}:
            assert event["saved_input_tokens"] == 0
        if event["surface"] in {"distill", "hook"} and event["baseline_input_tokens"] is not None:
            expected = max(event["baseline_input_tokens"] - event["delivered_input_tokens"], 0)
            assert event["saved_input_tokens"] == expected == event["dropped_input_tokens"]
        if (
            event["surface"] == "mcp"
            and event["evidence_kind"] == "inferred"
            and event["result_state"] in {"success", "partial"}
            and event["baseline_input_tokens"] is not None
        ):
            expected = max(event["baseline_input_tokens"] - event["delivered_input_tokens"], 0)
            assert event["saved_input_tokens"] == expected
        if (
            event["surface"] == "mcp"
            and event["evidence_kind"] == "measured"
            and event["result_state"] in {"success", "partial"}
            and event["baseline_input_tokens"] is not None
        ):
            expected = max(event["baseline_input_tokens"] - event["delivered_input_tokens"], 0)
            assert event["saved_input_tokens"] == expected == event["dropped_input_tokens"]
        if (
            event["pre_budget_input_tokens"] is not None
            and event["delivered_input_tokens"] is not None
            and event["dropped_input_tokens"] is not None
        ):
            assert event["dropped_input_tokens"] == max(
                event["pre_budget_input_tokens"] - event["delivered_input_tokens"], 0
            )
        if event["baseline_output_tokens"] is not None:
            assert event["saved_output_tokens"] == max(
                event["baseline_output_tokens"] - event["delivered_output_tokens"], 0
            )
        if event["surface"] == "mcp" and event["request_id"] is not None:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", event["request_id"])
        if event["surface"] == "vscode_lm":
            UUID(event["request_id"])
        if (
            event["input_rate_usd_per_million"] is not None
            or event["output_rate_usd_per_million"] is not None
        ):
            assert event["currency"] == "USD"

        metadata = event.get("metadata", {})
        assert set(metadata) <= {"client_info_normalized", "identity_mapping_version"}
        assert all(isinstance(value, str) and len(value) <= 64 for value in metadata.values())
        forbidden = {
            "prompt",
            "transcript",
            "command",
            "raw_command",
            "header",
            "headers",
            "secret",
        }
        assert forbidden.isdisjoint(event)
        assert forbidden.isdisjoint(metadata)

    model_a = [event for event in events if event["model"] == "fixture-model-a"]
    assert {
        (event["pricing_version"], event["input_rate_usd_per_million"]) for event in model_a
    } >= {
        ("2026-08-01", 2.0),
        ("2026-08-15", 3.0),
    }

    generic = next(
        event for event in events if event.get("metadata", {}).get("client_info_normalized")
    )
    assert (
        generic["metadata"]["client_info_normalized"]
        not in payload["identity_mapping"]["recognized"]
    )
    assert generic["integration"] == generic["agent"] == payload["identity_mapping"]["fallback"]


@pytest.mark.parametrize(("scope", "days"), [("all", None), ("30d", 30), ("7d", 7)])
def test_fixture_totals_are_hand_calculable(scope: str, days: int | None) -> None:
    payload = _load()
    as_of = datetime.fromisoformat(payload["as_of"])
    actual = _totals(_scope(_unique_events(payload), as_of, days))
    expected = payload["expected"][scope]

    for key, value in actual.items():
        if isinstance(value, float):
            assert value == pytest.approx(expected[key])
        else:
            assert value == expected[key]


@pytest.mark.parametrize(("scope", "days"), [("all", None), ("30d", 30), ("7d", 7)])
def test_opportunities_are_windowed_and_excluded_from_achieved_savings(
    scope: str, days: int | None
) -> None:
    payload = _load()
    as_of = datetime.fromisoformat(payload["as_of"])
    opportunities = _scope(payload["opportunities"], as_of, days)
    opportunity_tokens = sum(item["estimated_potential_input_tokens"] for item in opportunities)

    assert len(opportunities) == payload["expected"][scope]["opportunity_count"]
    assert opportunity_tokens == payload["expected"][scope]["opportunity_tokens_excluded"]
    assert opportunity_tokens not in {
        payload["expected"][window]["saved_input_tokens"] for window in ("all", "30d", "7d")
    }

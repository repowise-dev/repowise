"""Durable chat artifact envelopes and legacy-row compatibility."""

from __future__ import annotations

from repowise.server.chat_artifacts import (
    create_artifact_envelope,
    find_artifact,
    normalize_message_artifacts,
    set_artifact_pinned,
)


def test_new_envelope_has_a_stable_id_and_one_payload() -> None:
    payload = {"targets": {"src/a.py": {"trend": "increasing"}}}

    artifact = create_artifact_envelope(
        tool_name="get_risk",
        artifact_type="risk",
        presentation="risk",
        data=payload,
        title="Risk assessment",
        evidence_basis="measured",
    )
    content = {
        "tool_calls": [
            {
                "id": "tool-1",
                "name": "get_risk",
                "artifact": artifact,
            }
        ]
    }

    restored = normalize_message_artifacts(content, message_id="message-1")

    assert restored["tool_calls"][0]["artifact"]["id"] == artifact["id"]
    assert restored["tool_calls"][0]["artifact"]["data"] is payload
    assert restored["tool_calls"][0]["artifact"]["evidence"]["basis"] == "measured"
    assert "result" not in restored["tool_calls"][0]


def test_legacy_result_restores_with_a_deterministic_artifact_id() -> None:
    legacy = {
        "tool_calls": [
            {
                "id": "tool-1",
                "name": "get_context",
                "result": {"targets": {}},
                "artifact_type": "context",
            }
        ]
    }

    first = normalize_message_artifacts(legacy, message_id="message-1")
    second = normalize_message_artifacts(legacy, message_id="message-1")

    first_call = first["tool_calls"][0]
    assert first_call["artifact"]["id"] == second["tool_calls"][0]["artifact"]["id"]
    assert first_call["artifact"]["type"] == "context"
    assert first_call["artifact"]["data"] == {"targets": {}}
    assert "result" not in first_call
    assert "artifact_type" not in first_call


def test_pin_upgrades_a_legacy_row_without_duplicating_payload() -> None:
    legacy = {"tool_calls": [{"id": "tool-1", "name": "get_health", "result": {"score": 8.4}}]}
    normalized = normalize_message_artifacts(legacy, message_id="message-1")
    artifact_id = normalized["tool_calls"][0]["artifact"]["id"]

    updated, found = set_artifact_pinned(
        legacy,
        message_id="message-1",
        artifact_id=artifact_id,
        pinned=True,
    )

    assert found is True
    call = updated["tool_calls"][0]
    assert call["artifact"]["pinned"] is True
    assert call["artifact"]["data"] == {"score": 8.4}
    assert "result" not in call


def test_find_artifact_handles_malformed_rows_safely() -> None:
    content = {"tool_calls": [None, {"artifact": "bad"}, {"artifact": {"id": "a1"}}]}

    assert find_artifact(content, message_id="message-1", artifact_id="missing") is None


def test_evidence_normalizes_real_coverage_and_collection_limits() -> None:
    artifact = create_artifact_envelope(
        tool_name="get_health",
        artifact_type="health",
        presentation="health",
        evidence_basis="measured",
        data={
            "top_findings": [{"id": "f1"}],
            "top_findings_total": 9,
            "coverage": {"summary": {}, "files": [], "files_total": 4, "files_emitted": 0},
        },
    )

    assert artifact["evidence"]["coverage"] == {"available": True}
    assert artifact["evidence"]["limits"]["collections"] == [
        {"name": "top findings", "total": 9, "emitted": 1},
        {"name": "coverage files", "total": 4, "emitted": 0},
    ]

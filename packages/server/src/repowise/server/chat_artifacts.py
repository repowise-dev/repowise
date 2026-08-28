"""Versioned artifact envelopes stored inside existing chat message JSON."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

ARTIFACT_ENVELOPE_VERSION = 1

_LEGACY_ARTIFACT_TYPE_BY_TOOL = {
    "get_overview": "overview",
    "get_context": "context",
    "get_symbol": "source",
    "get_risk": "risk",
    "get_change_risk": "change_risk",
    "get_why": "decisions",
    "search_codebase": "search_results",
    "get_health": "health",
    "get_dead_code": "dead_code",
    "get_dependency_path": "dependency_path",
    "get_execution_flows": "call_path",
    "get_architecture_diagram": "diagram",
}


def _legacy_artifact_id(message_id: str, tool_id: str, index: int) -> str:
    return uuid5(
        NAMESPACE_URL,
        f"repowise:chat-artifact:{message_id}:{tool_id}:{index}",
    ).hex


def _evidence_qualifiers(data: dict[str, Any]) -> dict[str, Any]:
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    state = meta.get("state") if isinstance(meta.get("state"), dict) else {}
    evidence: dict[str, Any] = {
        "basis": data.get("evidence_kind") or data.get("basis") or "unknown",
    }
    confidence = data.get("confidence")
    if confidence is not None:
        evidence["confidence"] = confidence
    coverage = data.get("coverage")
    if coverage is not None:
        if isinstance(coverage, dict):
            evidence["coverage"] = {
                "available": bool(coverage.get("available"))
                if "available" in coverage
                else "summary" in coverage or "files" in coverage
            }
        else:
            evidence["coverage"] = {"available": bool(coverage)}
    if state.get("truncated") or meta.get("omitted") or data.get("truncated"):
        evidence["truncated"] = True
    stale_warning = meta.get("stale_warning") or data.get("stale_warning")
    if stale_warning:
        evidence["stale"] = str(stale_warning)
    collections: list[dict[str, Any]] = []
    for key, total in data.items():
        if not key.endswith("_total"):
            continue
        prefix = key.removesuffix("_total")
        emitted = data.get(f"{prefix}_emitted")
        if emitted is None and isinstance(data.get(prefix), list):
            emitted = len(data[prefix])
        if emitted is not None:
            collections.append(
                {"name": prefix.replace("_", " "), "total": total, "emitted": emitted}
            )
    coverage_data = data.get("coverage")
    if isinstance(coverage_data, dict):
        total = coverage_data.get("files_total")
        emitted = coverage_data.get("files_emitted")
        if total is not None and emitted is not None:
            collections.append({"name": "coverage files", "total": total, "emitted": emitted})
    if collections:
        evidence["limits"] = {"collections": collections}
    return evidence


def create_artifact_envelope(
    *,
    tool_name: str,
    artifact_type: str,
    presentation: str,
    data: dict[str, Any],
    title: str | None = None,
    artifact_id: str | None = None,
    pinned: bool = False,
    evidence_basis: str = "unknown",
) -> dict[str, Any]:
    """Create the one durable payload stored for a completed tool call."""
    return {
        "id": artifact_id or uuid4().hex,
        "version": ARTIFACT_ENVELOPE_VERSION,
        "type": artifact_type,
        "tool_name": tool_name,
        "title": title or tool_name,
        "presentation": presentation,
        "data": data,
        "evidence": {
            **_evidence_qualifiers(data),
            "basis": data.get("evidence_kind") or data.get("basis") or evidence_basis,
        },
        "pinned": pinned,
        "created_at": datetime.now(UTC).isoformat(),
    }


def normalize_message_artifacts(
    content: dict[str, Any],
    *,
    message_id: str,
) -> dict[str, Any]:
    """Return a single-payload envelope view for new and historical rows."""
    normalized = dict(content)
    raw_calls = content.get("tool_calls")
    if not isinstance(raw_calls, list):
        return normalized

    calls: list[Any] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            calls.append(raw_call)
            continue
        call = dict(raw_call)
        artifact = call.get("artifact")
        result = call.get("result")
        if isinstance(artifact, dict) and isinstance(artifact.get("data"), dict):
            envelope = dict(artifact)
            envelope.setdefault(
                "id",
                _legacy_artifact_id(message_id, str(call.get("id", "tool")), index),
            )
            envelope.setdefault("version", ARTIFACT_ENVELOPE_VERSION)
            envelope.setdefault("type", call.get("artifact_type") or "generic")
            envelope.setdefault("tool_name", str(call.get("name", "unknown")))
            envelope.setdefault("presentation", envelope["type"])
            envelope.setdefault("title", call.get("summary") or envelope["tool_name"])
            envelope.setdefault("evidence", _evidence_qualifiers(envelope["data"]))
            envelope.setdefault("pinned", False)
            call["artifact"] = envelope
            call.pop("result", None)
            call.pop("artifact_type", None)
        elif isinstance(result, dict):
            tool_name = str(call.get("name", "unknown"))
            artifact_type = str(
                call.get("artifact_type") or _LEGACY_ARTIFACT_TYPE_BY_TOOL.get(tool_name, "generic")
            )
            call["artifact"] = create_artifact_envelope(
                tool_name=tool_name,
                artifact_type=artifact_type,
                presentation=artifact_type,
                data=result,
                title=str(call.get("summary") or tool_name),
                artifact_id=_legacy_artifact_id(
                    message_id,
                    str(call.get("id", "tool")),
                    index,
                ),
            )
            call.pop("result", None)
            call.pop("artifact_type", None)
        calls.append(call)
    normalized["tool_calls"] = calls
    return normalized


def find_artifact(
    content: dict[str, Any],
    *,
    message_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    normalized = normalize_message_artifacts(content, message_id=message_id)
    for call in normalized.get("tool_calls", []):
        if not isinstance(call, dict):
            continue
        artifact = call.get("artifact")
        if isinstance(artifact, dict) and artifact.get("id") == artifact_id:
            return artifact
    return None


def set_artifact_pinned(
    content: dict[str, Any],
    *,
    message_id: str,
    artifact_id: str,
    pinned: bool,
) -> tuple[dict[str, Any], bool]:
    normalized = normalize_message_artifacts(content, message_id=message_id)
    found = False
    for call in normalized.get("tool_calls", []):
        if not isinstance(call, dict):
            continue
        artifact = call.get("artifact")
        if isinstance(artifact, dict) and artifact.get("id") == artifact_id:
            artifact["pinned"] = pinned
            found = True
            break
    return normalized, found

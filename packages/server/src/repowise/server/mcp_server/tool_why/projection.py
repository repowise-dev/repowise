"""Decision rows as get_why serves them: authority, ordering, projection."""

from __future__ import annotations

import json
from typing import Any

from repowise.core.analysis.decisions.lifecycle import status_rank
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    cap_collection,
)
from repowise.server.mcp_server._helpers import (
    _decision_body,
)
from repowise.server.mcp_server.tool_why.caps import _MAX_AFFECTED_FILES


def _authority_of(decision_id: str, accepted: set[str]) -> str:
    """``"accepted"`` when an acceptance binds this record, else ``"candidate"``.

    Membership of the acceptance set, never ``status`` (see
    :func:`~repowise.core.persistence.crud.authority.decision_currencies`).
    Stamped on every emitted row so a ruling reads apart from a guess.
    """
    return "accepted" if decision_id in accepted else "candidate"


def _path_decision_sort_key(d: Any) -> tuple[int, float, float]:
    return (
        status_rank(d.status),
        -(d.confidence or 0.0),
        d.staleness_score or 0.0,
    )


def _governing_decision_entry(
    d: Any,
    affected_files: list,
    lineage: list[dict],
    collector: OmissionCollector | None = None,
    accepted: set[str] | None = None,
) -> dict:
    """Serialize a decision that governs a path, including its lineage chain."""
    entry = {
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "authority": _authority_of(d.id, accepted or set()),
        "context": d.context,
        "decision": _decision_body(d),
        "rationale": d.rationale,
        "alternatives": json.loads(d.alternatives_json),
        "consequences": json.loads(d.consequences_json),
        "affected_files": affected_files[:_MAX_AFFECTED_FILES],
        "source": d.source,
        "confidence": d.confidence,
        "staleness_score": d.staleness_score,
        "lineage": lineage if len(lineage) > 1 else [],
    }
    _cap_affected_files(entry, d.id, affected_files, collector)
    return entry


def _cap_affected_files(
    entry: dict,
    decision_id: str,
    affected_files: list,
    collector: OmissionCollector | None,
) -> None:
    """Bank the paths past the head *entry* serves, and count them on it."""
    if len(affected_files) > _MAX_AFFECTED_FILES:
        cap_collection(
            entry,
            "affected_files",
            affected_files,
            _MAX_AFFECTED_FILES,
            collector,
            label=f"decision {decision_id} :: affected_files beyond cap={_MAX_AFFECTED_FILES}",
        )


def _merge_decisions(
    keyword_matches: list[tuple[Any, list[str]]],
    lineage_by_id: dict[str, list[dict]],
    collector: OmissionCollector | None = None,
    accepted: set[str] | None = None,
) -> list[dict]:
    """Project collapsed keyword hits, best-first.

    *keyword_matches* arrives from :func:`_collapse_restatements`, so the folded
    ids ride along on ``restates``: nothing becomes unaddressable and the store
    is untouched.
    """
    seen_ids: set[str] = set()
    merged_decisions: list[dict] = []
    for d, folded in keyword_matches:
        if d.id in seen_ids:
            continue
        seen_ids.update([d.id, *folded])
        affected_files = json.loads(d.affected_files_json)
        entry = {
            "id": d.id,
            "title": d.title,
            "status": d.status,
            "authority": _authority_of(d.id, accepted or set()),
            "decision": _decision_body(d),
            "rationale": d.rationale,
            "context": d.context,
            "consequences": json.loads(d.consequences_json),
            # A head plus a total; see ``_MAX_AFFECTED_FILES``.
            "affected_files": affected_files[:_MAX_AFFECTED_FILES],
            "source": d.source,
            "confidence": d.confidence,
            "lineage": lineage_by_id.get(d.id, []),
        }
        _cap_affected_files(entry, d.id, affected_files, collector)
        if folded:
            entry["restates"] = folded
        merged_decisions.append(entry)
    return merged_decisions


#: A quoted rule or background claim. Enough for the ruling; the rest is one id call away.
_QUOTED_DECISION_CHARS = 300

#: What a background row leaves out: it is context, not a rule, so it stays small.
_BACKGROUND_DETAIL_FIELDS = (
    "context",
    "consequences",
    "alternatives",
    "staleness_score",
    "review_state",
)


def bounded(text: str) -> str:
    """*text* cut to :data:`_QUOTED_DECISION_CHARS`, marked when cut."""
    if len(text) > _QUOTED_DECISION_CHARS:
        return text[: _QUOTED_DECISION_CHARS - 3].rstrip() + "..."
    return text


def trim_background(rows: list[dict]) -> None:
    """Drop the detail fields from every row not in force, in place."""
    for row in rows:
        if isinstance(row, dict) and row.get("authority") != "accepted":
            for key in _BACKGROUND_DETAIL_FIELDS:
                row.pop(key, None)


#: The lanes whose rows are decision records.
_DECISION_LANES = (
    "decisions",
    "candidates",
    "history",
    "governing_decisions",
    "candidate_decisions",
    "stale_decisions",
    "proposed_awaiting_review",
)

#: What a served evidence ref keeps; ``get_why(id=...)`` returns the full one.
_REF_KEYS = ("id", "kind", "commit", "commit_prefix", "path", "range", "line", "verification")


def lean_response(value: Any, *, keep_repository: bool = False) -> Any:
    """Drop empty decision fields and shorten evidence refs and commits, in place.

    A ref keeps ``provenance`` and ``source`` only where they differ from its
    row's, and ``repository`` only where several repositories are served.
    """
    if isinstance(value, list):
        for item in value:
            lean_response(item, keep_repository=keep_repository)
    elif isinstance(value, dict):
        _lean_row(value, keep_repository)
        for key, child in value.items():
            if key in _DECISION_LANES and isinstance(child, list):
                _drop_empty_fields(child)
            if key != "evidence_refs":
                lean_response(child, keep_repository=keep_repository)
    return value


def _lean_row(value: dict, keep_repository: bool) -> None:
    refs = value.get("evidence_refs")
    if isinstance(refs, list):
        value["evidence_refs"] = [_lean_ref(r, value, keep_repository) for r in refs]
    if isinstance(value.get("commit"), str):
        value["commit"] = value["commit"][:12]


def _lean_ref(ref: Any, row: dict, keep_repository: bool) -> Any:
    if not isinstance(ref, dict):
        return ref
    keys = (*_REF_KEYS, "repository") if keep_repository else _REF_KEYS
    out = {k: ref[k] for k in keys if k in ref}
    # Only where a row cites several kinds of evidence.
    for k in ("provenance", "source"):
        if row.get(k) is not None and ref.get(k) not in (None, row[k]):
            out[k] = ref[k]
    if isinstance(out.get("commit"), str):
        out["commit"] = out["commit"][:12]
    return out


def _drop_empty_fields(rows: list) -> None:
    for row in rows:
        if isinstance(row, dict):
            for key in [k for k, v in row.items() if v in ("", None) or v == [] or v == {}]:
                del row[key]

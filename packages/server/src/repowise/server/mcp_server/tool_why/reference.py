"""Reference mode: resolve one emitted decision or evidence id."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from repowise.core.persistence.crud.authority import (
    AmbiguousDecisionIdError,
    resolve_decision_id,
    resolve_decision_id_prefix,
)
from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    filter_path_list,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._why_evidence import annotate_response_evidence_async
from repowise.server.mcp_server.tool_why.loading import _attach_decision_evidence, _load_corpus
from repowise.server.mcp_server.tool_why.projection import _governing_decision_entry


async def _why_reference(
    reference_id: str,
    repo: str | None,
    *,
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one emitted decision/evidence id without relevance search."""

    ctx, repository, records, _target_git, accepted = await _load_corpus(repo, None)
    async with get_session(ctx.session_factory) as session:
        await _attach_decision_evidence(session, records)
        resolved_id, candidates = await _resolve_reference_id(session, reference_id, records)

    meta = _build_meta(repository=repository)
    if candidates:
        from repowise.server.mcp_server._savings.interaction import error_reply

        return error_reply(
            "invalid_args",
            {
                "mode": "reference",
                "reference_id": reference_id,
                "resolved": False,
                "ambiguous": True,
                "candidates": candidates,
                "error": f"Decision id prefix {reference_id!r} is ambiguous; candidates: {', '.join(candidates)}",
                "decisions": [],
                "evidence_refs": [],
                "_meta": meta,
            },
        )

    reference_id = resolved_id

    payload = {
        "decisions": [
            _governing_decision_entry(
                record,
                filter_path_list(
                    json.loads(record.affected_files_json or "[]"),
                    _get_exclude_spec(ctx.path),
                ),
                [],
                accepted=accepted,
            )
            for record in records
        ]
    }
    await annotate_response_evidence_async(
        payload,
        ctx.alias,
        records,
        repo_root=ctx.path,
    )
    matches = [
        row
        for row in payload["decisions"]
        if row["id"] == reference_id
        or any(ref["id"] == reference_id for ref in row.get("evidence_refs", []))
    ]
    matching_refs = _matching_evidence_refs(matches, reference_id, reference, ctx)
    persistence = (payload.get("_meta") or {}).get("reference_persistence")
    if persistence is not None:
        meta["reference_persistence"] = persistence
    return {
        "mode": "reference",
        "reference_id": reference_id,
        "resolved": bool(matches or matching_refs),
        "decisions": matches,
        "evidence_refs": list(matching_refs.values()),
        "_meta": meta,
    }


_MIN_PREFIX_LENGTH: Final[int] = 8


async def _resolve_reference_id(
    session: Any, reference_id: str, records: list
) -> tuple[str, list[str]]:
    """Resolve an exact or prefix decision id, following aliases or identifying ambiguous candidates."""
    if reference_id.startswith("ev_"):
        return reference_id, []

    # 1. Exact match on live records
    if any(record.id == reference_id for record in records):
        return reference_id, []

    # 2. Exact match on retired alias or direct ID in DB
    direct = await resolve_decision_id(session, reference_id)
    if direct and direct != reference_id:
        return direct, []

    # Reject reference ids shorter than 8 characters before prefix branches run
    if len(reference_id) < _MIN_PREFIX_LENGTH:
        return reference_id, []

    # 3. Prefix match on live records
    matching_records = sorted({r.id for r in records if r.id.startswith(reference_id)})
    if len(matching_records) == 1:
        return matching_records[0], []
    if len(matching_records) > 1:
        return reference_id, matching_records

    # 4. Prefix match via shared persistence helper (handles DecisionRecord & non-superseded DecisionAlias)
    try:
        full_id = await resolve_decision_id_prefix(
            session, reference_id, min_length=_MIN_PREFIX_LENGTH
        )
        if full_id is not None:
            return full_id, []
    except AmbiguousDecisionIdError as exc:
        return reference_id, exc.candidates

    return reference_id, []


def _matching_evidence_refs(
    matches: list[dict[str, Any]],
    reference_id: str,
    reference: dict[str, Any] | None,
    ctx: Any,
) -> dict[str, dict[str, Any]]:
    """Evidence refs answering *reference_id*, keyed by id.

    From the matched rows first; an ``ev_`` id no row carries is looked up in
    the evidence stores; a caller's structured reference is echoed back only
    once something matched and its coordinates reproduce its own id.
    """
    matching_refs = {
        ref["id"]: ref
        for row in matches
        for ref in row.get("evidence_refs", [])
        if ref["id"] == reference_id
    }
    if not matches and reference_id.startswith("ev_"):
        live_ref = _resolve_unattached_evidence_reference(
            reference_id, ctx.alias, ctx.path
        )
        if live_ref is not None:
            matching_refs[live_ref["id"]] = live_ref
    if (
        reference is not None
        and matching_refs
        and _evidence_reference_matches_id(reference)
    ):
        matching_refs[reference_id] = dict(reference)
    return matching_refs


def _evidence_reference_matches_id(value: dict[str, Any]) -> bool:
    """Validate the identity-bearing coordinates of a structured evidence ref."""

    from repowise.server.mcp_server._references import reference as build_reference

    identifier = value.get("id")
    repository = value.get("repository")
    kind = value.get("kind")
    if not all(isinstance(item, str) for item in (identifier, repository, kind)):
        return False
    coordinate_keys = {
        "commit": ("commit",),
        "file_range": ("path", "range"),
        "file_content": ("path", "line", "content_id"),
        "legacy": ("path", "content_id", "commit_prefix"),
    }.get(kind)
    if coordinate_keys is None:
        return False
    coordinates = {key: value[key] for key in coordinate_keys if key in value}
    return build_reference(kind, repository, **coordinates)["id"] == identifier


def _resolve_unattached_evidence_reference(
    reference_id: str,
    repository: str,
    repo_root: str | Path,
) -> dict[str, Any] | None:
    """Resolve evidence not attached to a persisted decision without a repo scan."""

    from repowise.server.mcp_server._references import repository_identity
    from repowise.server.mcp_server._why_evidence import (
        resolve_cached_evidence_reference,
        resolve_persisted_evidence_reference,
    )

    repository = repository_identity(repository)
    return resolve_cached_evidence_reference(
        repository, reference_id
    ) or resolve_persisted_evidence_reference(repository, reference_id, repo_root)

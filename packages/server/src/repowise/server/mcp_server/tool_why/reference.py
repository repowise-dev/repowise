"""Reference mode: resolve one emitted decision or evidence id."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repowise.core.persistence.crud.authority import resolve_decision_id
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
        reference_id = await _follow_moved_decision_id(session, reference_id, records)

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
    meta = _build_meta(repository=repository)
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


async def _follow_moved_decision_id(
    session: Any, reference_id: str, records: list
) -> str:
    """The live id for a decision id that no longer names a live record."""
    # This tool tells callers to hold onto the ids it emits, so an id quoted
    # from an earlier session may name a decision that has since moved.
    # Follow the alias for one that no longer matches anything live; a live
    # id is left alone, so a merged candidate still answers about itself.
    stale_decision_id = not reference_id.startswith("ev_") and not any(
        record.id == reference_id for record in records
    )
    if stale_decision_id:
        reference_id = await resolve_decision_id(session, reference_id) or reference_id
    return reference_id


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

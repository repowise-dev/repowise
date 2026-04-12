"""MCP Tool 9: update_decision_records — CRUD operations on architectural decision records."""

from __future__ import annotations

import json
from typing import Any

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionRecord
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._server import mcp

_VALID_ACTIONS = frozenset({"create", "update", "update_status", "delete", "list", "get"})


def _serialize_decision(rec: DecisionRecord) -> dict[str, Any]:
    """Convert a DecisionRecord ORM object to a plain dict."""
    return {
        "id": rec.id,
        "repository_id": rec.repository_id,
        "title": rec.title,
        "status": rec.status,
        "context": rec.context,
        "decision": rec.decision,
        "rationale": rec.rationale,
        "alternatives": json.loads(rec.alternatives_json),
        "consequences": json.loads(rec.consequences_json),
        "affected_files": json.loads(rec.affected_files_json),
        "affected_modules": json.loads(rec.affected_modules_json),
        "tags": json.loads(rec.tags_json),
        "source": rec.source,
        "evidence_file": rec.evidence_file,
        "confidence": rec.confidence,
        "staleness_score": rec.staleness_score,
        "superseded_by": rec.superseded_by,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }


@mcp.tool()
async def update_decision_records(
    action: str,
    repo: str | None = None,
    # --- Identifier ---
    decision_id: str | None = None,
    # --- Content fields (for create / update) ---
    title: str | None = None,
    status: str | None = None,
    context: str | None = None,
    decision: str | None = None,
    rationale: str | None = None,
    alternatives: list[str] | None = None,
    consequences: list[str] | None = None,
    affected_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    tags: list[str] | None = None,
    # --- Status change ---
    superseded_by: str | None = None,
    # --- List filters ---
    filter_status: str | None = None,
    filter_source: str | None = None,
    filter_tag: str | None = None,
    filter_module: str | None = None,
    include_proposed: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Create, update, or manage architectural decision records.

    Six actions:
    1. create  — Record a new decision. Requires `title`. Optional: context,
       decision, rationale, alternatives, consequences, affected_files,
       affected_modules, tags, status (defaults to "proposed").
    2. update  — Update content fields of an existing decision by `decision_id`.
       Pass only the fields you want to change.
    3. update_status — Change the status of a decision. Requires `decision_id`
       and `status` (proposed | active | deprecated | superseded).
       Optionally pass `superseded_by` with the ID of the replacement decision.
    4. delete  — Remove a decision by `decision_id`.
    5. list    — List decisions. Optional filters: filter_status, filter_source,
       filter_tag, filter_module, include_proposed, limit, offset.
    6. get     — Get a single decision by `decision_id`.

    Always call this after making architectural changes to keep decision records
    current. Use action="create" to record new decisions and action="update" to
    refine existing ones.

    Args:
        action: One of: create, update, update_status, delete, list, get.
        repo: Repository path, name, or ID.
        decision_id: Required for get, update, update_status, delete.
        title: Decision title (required for create).
        status: Decision status (for create defaults to "proposed").
        context: What forced this decision.
        decision: What was chosen.
        rationale: Why this was chosen.
        alternatives: Rejected alternatives.
        consequences: Tradeoffs / consequences.
        affected_files: File paths affected by this decision.
        affected_modules: Module paths affected.
        tags: Category tags (e.g. auth, database, api, performance).
        superseded_by: ID of the replacement decision (for update_status).
        filter_status: Filter list by status.
        filter_source: Filter list by source.
        filter_tag: Filter list by tag.
        filter_module: Filter list by module.
        include_proposed: Include proposed decisions in list (default True).
        limit: Max results for list (default 50).
        offset: Offset for list pagination.
    """
    if repo == "all":
        return _unsupported_repo_all("update_decision_records")
    ctx = await _resolve_repo_context(repo)

    if action not in _VALID_ACTIONS:
        return {
            "error": f"Unknown action {action!r}. Valid actions: {', '.join(sorted(_VALID_ACTIONS))}"
        }

    # --- create ---
    if action == "create":
        if not title:
            return {"error": "action 'create' requires 'title'"}
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            rec = await crud.upsert_decision(
                session,
                repository_id=repository.id,
                title=title,
                status=status or "proposed",
                context=context or "",
                decision=decision or "",
                rationale=rationale or "",
                alternatives=alternatives,
                consequences=consequences,
                affected_files=affected_files,
                affected_modules=affected_modules,
                tags=tags,
                source="mcp_tool",
                confidence=1.0,
            )
            return {"action": "created", "decision": _serialize_decision(rec)}

    # --- get ---
    if action == "get":
        if not decision_id:
            return {"error": "action 'get' requires 'decision_id'"}
        async with get_session(ctx.session_factory) as session:
            rec = await crud.get_decision(session, decision_id)
            if rec is None:
                return {"error": f"Decision {decision_id} not found"}
            return {"action": "get", "decision": _serialize_decision(rec)}

    # --- list ---
    if action == "list":
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            decisions = await crud.list_decisions(
                session,
                repository.id,
                status=filter_status,
                source=filter_source,
                tag=filter_tag,
                module=filter_module,
                include_proposed=include_proposed,
                limit=limit,
                offset=offset,
            )
            return {
                "action": "list",
                "count": len(decisions),
                "decisions": [_serialize_decision(d) for d in decisions],
            }

    # --- update ---
    if action == "update":
        if not decision_id:
            return {"error": "action 'update' requires 'decision_id'"}
        fields: dict[str, Any] = {}
        if title is not None:
            fields["title"] = title
        if context is not None:
            fields["context"] = context
        if decision is not None:
            fields["decision"] = decision
        if rationale is not None:
            fields["rationale"] = rationale
        if alternatives is not None:
            fields["alternatives"] = alternatives
        if consequences is not None:
            fields["consequences"] = consequences
        if affected_files is not None:
            fields["affected_files"] = affected_files
        if affected_modules is not None:
            fields["affected_modules"] = affected_modules
        if tags is not None:
            fields["tags"] = tags
        if not fields:
            return {"error": "action 'update' requires at least one field to change"}
        async with get_session(ctx.session_factory) as session:
            rec = await crud.update_decision_by_id(session, decision_id, **fields)
            if rec is None:
                return {"error": f"Decision {decision_id} not found"}
            return {"action": "updated", "decision": _serialize_decision(rec)}

    # --- update_status ---
    if action == "update_status":
        if not decision_id:
            return {"error": "action 'update_status' requires 'decision_id'"}
        if not status:
            return {"error": "action 'update_status' requires 'status'"}
        async with get_session(ctx.session_factory) as session:
            try:
                rec = await crud.update_decision_status(
                    session,
                    decision_id,
                    status,
                    superseded_by=superseded_by,
                )
            except ValueError as exc:
                return {"error": str(exc)}
            if rec is None:
                return {"error": f"Decision {decision_id} not found"}
            return {"action": "status_updated", "decision": _serialize_decision(rec)}

    # --- delete ---
    if action == "delete":
        if not decision_id:
            return {"error": "action 'delete' requires 'decision_id'"}
        async with get_session(ctx.session_factory) as session:
            deleted = await crud.delete_decision(session, decision_id)
            if not deleted:
                return {"error": f"Decision {decision_id} not found"}
            return {"action": "deleted", "decision_id": decision_id}

    return {"error": f"Unhandled action {action!r}"}

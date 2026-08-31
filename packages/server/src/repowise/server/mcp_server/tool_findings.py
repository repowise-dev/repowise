"""MCP tool: set_finding_status — record a disposition on a refactoring plan.

The missing write half of the findings lifecycle (#1535). ``get_health``
surfaces refactoring plans read-only, and the generated task prompts tell an
agent to "flag false positives" — but until now there was nowhere to flag
them *to* from inside the agent loop. This tool closes the loop: an agent
that judged a plan wrong records ``false_positive`` (or ``acknowledged`` /
``resolved``), and the finalizer ("finalize_refactoring_suggestions") never
re-emits a ``false_positive`` plan, so the triage is durable across runs —
the same suppression the REST PATCH path gives the web UI.
"""

from __future__ import annotations

from typing import Any

from repowise.core.persistence.crud import (
    get_refactoring_suggestion,
    get_refactoring_suggestions,
    update_refactoring_suggestion_status,
)
from repowise.core.persistence.database import get_session
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
from repowise.server.mcp_server._meta import build_meta as _build_meta

# One triage vocabulary for every layer (shared with health findings and the
# REST PATCH surface) — see crud/analysis/refactoring.py.
ALLOWED_STATUSES = ("open", "acknowledged", "resolved", "false_positive")


@mcp.tool(
    default=False,
    tier="specialist",
    surface_order=235,
    trust_kind="user",
    artifact_type="finding",
    presentation="status",
    safety="mutating",
)
async def set_finding_status(
    suggestion_id: str,
    status: str,
    repo: str | None = None,
    reason: str = "agent",
) -> dict[str, Any]:
    """Record a durable disposition on one refactoring plan.

    The write side of the findings triage loop: after reviewing a plan from
    ``get_health(include=["refactoring"])`` (use the plan's ``id`` or its
    content-derived ``public_id``), record what you decided so the judgment
    survives every later analysis run.

    - ``false_positive`` — the plan is wrong for this repository; it is
      never re-emitted on future runs (the analyzer's finalizer suppresses
      the kernel), so a wrong suggestion stops coming back instead of being
      re-proposed forever.
    - ``acknowledged`` — real, but the team is consciously not acting now;
      stays visible, stops counting as unheard.
    - ``resolved`` — the change landed (or the code moved on); a plan a
      person resolved stays resolved even if the detector still fires.
    - ``open`` — reset a prior decision.

    ``reason`` is a free-text note stored on the row for audit. Returns the
    new status and its timestamp; raises on an unknown status or an id that
    does not belong to this repository.
    """
    if status not in ALLOWED_STATUSES:
        raise ValueError(
            f"unknown finding status: {status!r}; expected one of {ALLOWED_STATUSES}"
        )

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        row = await get_refactoring_suggestion(session, repository.id, suggestion_id)
        if row is None:
            # Same fallback as generate_refactoring_code: a deep link may carry
            # the display id ("<alias> <file>:<symbol>") rather than a storage
            # or content id — match it against the rendered plan ids.
            from repowise.server.mcp_server.tool_health import _refactoring_plan_id

            candidates = await get_refactoring_suggestions(session, repository.id)
            row = next(
                (
                    candidate
                    for candidate in candidates
                    if _refactoring_plan_id(candidate, ctx.alias or repository.name)
                    == suggestion_id
                ),
                None,
            )
        if row is None:
            raise ValueError(
                f"refactoring plan not found: {suggestion_id!r} (in this repository)"
            )
        updated = await update_refactoring_suggestion_status(
            session, repository.id, row.id, status, reason=reason
        )
        await session.commit()

    return {
        "id": updated.id,
        "public_id": updated.public_id,
        "status": updated.status,
        "status_reason": updated.status_reason,
        "status_changed_at": updated.status_changed_at.isoformat()
        if updated.status_changed_at
        else None,
        "note": (
            "Recorded. A false_positive plan will not be re-emitted by "
            "future analysis runs."
            if status == "false_positive"
            else "Recorded."
        ),
        "_meta": _build_meta(repository=repository),
    }

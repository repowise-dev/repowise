"""MCP tool: generate_refactoring_code — opt-in plan -> code + diff.

The deterministic refactoring layer surfaces structured plans through
``get_health(include=["refactoring"])``; each plan carries an ``id``. This tool
takes one such id and asks the configured LLM to produce the actual refactored
code and a unified diff, grounded on the plan plus the real source spans it
references. It is strictly opt-in (gated on ``refactoring.llm.enabled``) and
needs the working tree on disk, so it is a local-server capability.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.refactoring.recommendations import hydrate_recommendations
from repowise.core.persistence.crud import get_refactoring_suggestion
from repowise.core.persistence.database import get_session
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
from repowise.server.mcp_server._meta import build_meta as _build_meta


@mcp.tool(default=False, surface_order=240, trust_kind="generated")
async def generate_refactoring_code(suggestion_id: str, repo: str | None = None) -> dict:
    """Generate refactored code + a unified diff for one refactoring plan.

    Opt-in code generation: turns a deterministic plan (from
    ``get_health(include=["refactoring"])`` — use a plan's ``id``) into the
    actual named code and a git-style diff, grounded on the plan plus the real
    source spans it references. For Extract Class the result carries an LCOM4
    before/after self-check.

    Disabled by default — returns an ``error`` unless ``refactoring.llm.enabled``
    is set in the repo's ``.repowise/config.yaml``. Uses the repo's configured
    provider/model (BYO key) and caches by a content hash, so an unchanged plan
    never regenerates.

    Args:
        suggestion_id: The ``id`` of a plan from ``get_health(... "refactoring")``.
        repo: Repo alias / id / path.
    """
    from repowise.core.analysis.health.refactoring.llm import (
        build_enrichment_provider,
        enrich_suggestion,
        llm_enrichment_enabled,
    )
    from repowise.core.repo_config import load_repo_config

    ctx = await _resolve_repo_context(repo)
    repo_path = Path(ctx.path)

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        row = await get_refactoring_suggestion(session, repository.id, suggestion_id)
        if row is None:
            from repowise.core.persistence.crud import get_refactoring_suggestions
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
            return {
                "error": "not_found",
                "detail": f"No refactoring plan with id {suggestion_id!r} in this repo.",
                "_meta": _build_meta(repository=repository),
            }
        recommendation = (await hydrate_recommendations(session, repository.id, [row]))[0]
        sug = recommendation.suggestion
        meta = _build_meta(repository=repository)

    from repowise.server.mcp_server.tool_health import _serialize_refactoring

    public_plan = _serialize_refactoring(
        recommendation, ctx.alias or repository.name
    )
    if not llm_enrichment_enabled(load_repo_config(repo_path)):
        return {
            "suggestion_id": public_plan["id"],
            "resolved": True,
            "plan": public_plan,
            "generation": {
                "available": False,
                "reason": "disabled",
                "detail": (
                    "Refactoring code generation is opt-in. Set "
                    "'refactoring.llm.enabled: true' in .repowise/config.yaml to enable it."
                ),
            },
            "_meta": meta,
        }

    try:
        provider = build_enrichment_provider(repo_path)
    except ValueError as exc:
        return {"error": "no_provider", "detail": str(exc), "_meta": meta}

    result = await enrich_suggestion(sug, provider=provider, repo_path=repo_path)
    payload = result.to_dict()
    payload["suggestion_id"] = public_plan["id"]
    payload["resolved"] = True
    payload["_meta"] = meta
    return payload

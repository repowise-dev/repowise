"""MCP tool: generate_refactoring_code — opt-in plan -> code + diff.

The deterministic refactoring layer surfaces structured plans through
``get_health(include=["refactoring"])``; each plan carries an ``id``. This tool
takes one such id and asks the configured LLM to produce the actual refactored
code and a unified diff, grounded on the plan plus the real source spans it
references. It is strictly opt-in (gated on ``refactoring.llm.enabled``) and
needs the working tree on disk, so it is a local-server capability.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repowise.core.persistence.crud import get_refactoring_suggestion
from repowise.core.persistence.database import get_session
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
from repowise.server.mcp_server._meta import build_meta as _build_meta


def _row_to_suggestion(row: Any) -> Any:
    """Re-hydrate a persisted ORM row into a ``RefactoringSuggestion`` dataclass.

    Mirrors the web router's adapter; the enrichment engine reads the open
    ``plan`` / ``evidence`` / ``blast_radius`` dicts, which the DB stores as
    ``*_json`` text.
    """
    from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion

    def _loads(value: Any) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    sug = RefactoringSuggestion(
        refactoring_type=row.refactoring_type,
        file_path=row.file_path,
        target_symbol=row.target_symbol,
        line_start=row.line_start,
        line_end=row.line_end,
        plan=_loads(row.plan_json),
        evidence=_loads(row.evidence_json),
        impact_delta=row.impact_delta,
        effort_bucket=row.effort_bucket,
        blast_radius=_loads(row.blast_radius_json),
        confidence=row.confidence,
        source_biomarker=row.source_biomarker,
    )
    sug.id = row.id  # type: ignore[attr-defined]
    return sug


@mcp.tool()
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

    if not llm_enrichment_enabled(load_repo_config(repo_path)):
        return {
            "error": "disabled",
            "detail": (
                "Refactoring code generation is opt-in. Set 'refactoring.llm.enabled: "
                "true' in .repowise/config.yaml to enable it."
            ),
        }

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session, repo)
        row = await get_refactoring_suggestion(session, repository.id, suggestion_id)
        if row is None:
            return {
                "error": "not_found",
                "detail": f"No refactoring plan with id {suggestion_id!r} in this repo.",
                "_meta": _build_meta(repository=repository),
            }
        sug = _row_to_suggestion(row)
        meta = _build_meta(repository=repository)

    try:
        provider = build_enrichment_provider(repo_path)
    except ValueError as exc:
        return {"error": "no_provider", "detail": str(exc), "_meta": meta}

    result = await enrich_suggestion(sug, provider=provider, repo_path=repo_path)
    payload = result.to_dict()
    payload["_meta"] = meta
    return payload

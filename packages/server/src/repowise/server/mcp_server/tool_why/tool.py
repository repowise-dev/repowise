"""The ``get_why`` MCP tool: argument handling and dispatch to one mode."""

from __future__ import annotations

from typing import Any

from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _is_path,
    _is_workspace_mode,
    _unsupported_repo_all,
)
from repowise.server.mcp_server.tool_why.basis import _stamp_answer_basis
from repowise.server.mcp_server.tool_why.dashboard import _why_health_dashboard
from repowise.server.mcp_server.tool_why.path_mode import _why_path, _why_targets
from repowise.server.mcp_server.tool_why.reference import _why_reference
from repowise.server.mcp_server.tool_why.search import _why_search
from repowise.server.mcp_server.tool_why.workspace import _why_workspace_search


@mcp.tool(
    surface_order=70,
    artifact_type="decisions",
    presentation="decision_evidence",
    evidence_basis="inferred",
    recipes=(
        ToolRecipe(
            "read_rationale",
            'get_why(targets=["path"])',
            ("get_why",),
        ),
    ),
)
async def get_why(
    query: str | None = None,
    targets: list[str] | None = None,
    repo: str | None = None,
    id: str | None = None,
    reference: dict[str, Any] | None = None,
) -> dict:
    """Why this code is shaped this way — decision records + evidence commits.

    Call before refactors or pattern divergences. Query modes: a question
    ("why is auth using JWT?"), a file path (governing decisions + origin
    story + alignment score), a question anchored to targets, or no query
    (decision health dashboard). Falls back to git archaeology when no
    decisions exist for a path — never empty. Evidence-bearing rows carry an
    explicit ``provenance`` and self-contained ``evidence_refs``; matching ids
    mean shared evidence, not independent corroboration. Every decision row
    carries ``authority``: ``accepted`` means somebody signed it, ``candidate``
    means nobody has yet. ``answer_basis`` names the strongest lane the response
    rests on (decision, episode, rationale, archaeology, documentation,
    candidate); only ``decision`` is a ruling, and ``candidate`` is the weakest
    -- it means nothing cleared that bar.

    Args:
        query: question, file/module path, or omit for the dashboard.
        targets: optional file paths to anchor the search, or to ask about on
            their own when there is no query.
        repo: usually omitted.
        id: decision or ``ev_...`` evidence id emitted by this or another tool.
        reference: structured evidence reference. Its id and repository are
            accepted together without caller translation.
    """
    if reference:
        id, repo = _reference_coordinates(reference, id, repo)
    if id:
        if repo == "all":
            return _unsupported_repo_all("get_why (reference lookup)")
        return _stamp_answer_basis(await _why_reference(id, repo, reference=reference))

    # --- repo="all": search decisions across ALL repos ---
    if repo == "all":
        if not query:
            return _unsupported_repo_all("get_why (health dashboard)")
        return _stamp_answer_basis(await _why_workspace_search(query))

    # --- Mode 1: No query → the targets, or the health dashboard ---
    # Targets first: naming files and asking nothing asks about those files.
    if not query:
        if targets:
            return _stamp_answer_basis(await _why_targets(list(targets), repo))
        # An orientation call, not an answer: no basis to name.
        return await _why_health_dashboard(repo)

    # --- Mode 2: Path → decisions, origin story, alignment ---
    if _is_path(query):
        return _stamp_answer_basis(await _why_path(query, repo))

    # --- Mode 3: Natural language → target-aware search ---
    return _stamp_answer_basis(await _why_search(query, targets, repo))


def _reference_coordinates(
    reference: dict[str, Any], decision_id: str | None, repo: str | None
) -> tuple[str | None, str | None]:
    """Fill the id and repo a caller left unset from their structured reference."""
    if decision_id is None and isinstance(reference.get("id"), str):
        decision_id = reference["id"]
    if (
        repo is None
        and _is_workspace_mode()
        and isinstance(reference.get("repository"), str)
    ):
        repo = reference["repository"]
    return decision_id, repo

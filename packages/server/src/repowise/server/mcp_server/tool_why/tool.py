"""The ``get_why`` MCP tool: argument handling and dispatch to one mode."""

from __future__ import annotations

from typing import Any

from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _is_path,
    _is_workspace_mode,
    _unsupported_repo_all,
    attach_ignored_arguments,
    resolve_enum_argument,
)
from repowise.server.mcp_server.tool_why.basis import _stamp_answer_basis
from repowise.server.mcp_server.tool_why.dashboard import _why_health_dashboard
from repowise.server.mcp_server.tool_why.loading import _named_file
from repowise.server.mcp_server.tool_why.path_mode import _why_path, _why_targets
from repowise.server.mcp_server.tool_why.projection import lean_response
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
    include: list[str] | None = None,
) -> dict:
    """Why this code is shaped this way — decision records + evidence commits.

    Call before a refactor or pattern divergence. Query: a question, a file
    path, a question with ``targets``, or nothing (decision dashboard).
    A path gets the rules in force first, then labelled background records.
    ``authority: accepted`` means somebody signed the decision;
    ``answer_basis`` names the strongest evidence lane, and only
    ``decision`` is a ruling.

    Args:
        query: question, file/module path, or omit for the dashboard.
        targets: file paths to anchor the search.
        id: decision or ``ev_...`` evidence id from any tool.
        reference: structured evidence reference.
        repo: usually omitted.
        include: ``["history"]``: a path's origin story, commits, episodes.
    """
    ignored: list[dict[str, Any]] = []
    # A list, not a generator, so every unknown value is named.
    history = "history" in [
        resolve_enum_argument(v, _INCLUDE, argument="include", ignored=ignored)
        for v in include or []
    ]
    if reference:
        id, repo = _reference_coordinates(reference, id, repo)
    result = await _dispatch(query, targets, repo, id, reference, history)
    attach_ignored_arguments(result, ignored)
    return result


#: ``include`` blocks; path mode serves its constraints alone without one.
_INCLUDE = ("history",)


async def _dispatch(
    query: str | None,
    targets: list[str] | None,
    repo: str | None,
    id: str | None,
    reference: dict[str, Any] | None,
    history: bool,
) -> dict:
    early = await _without_question(query, targets, repo, id, reference, history)
    if early is not None:
        return early

    # --- Mode 2: Path → decisions, origin story, alignment ---
    if _is_path(query):
        return _stamp_answer_basis(_lean(await _why_path(query, repo, history=history)))

    # --- Mode 3: Natural language → target-aware search ---
    # A named file anchors as ``targets`` would; a failed lookup anchors nothing.
    named = None
    if not targets:
        try:
            named = await _named_file(query, repo)
        except Exception:
            named = None
    result = await _why_search(query, [named] if named else targets, repo)
    if named:
        result["anchored_on"] = named
    return _stamp_answer_basis(_lean(result))


async def _without_question(
    query: str | None,
    targets: list[str] | None,
    repo: str | None,
    id: str | None,
    reference: dict[str, Any] | None,
    history: bool,
) -> dict | None:
    """A reference lookup, a cross-repo search, targets alone or the dashboard; else None."""
    if id:
        if repo == "all":
            return _unsupported_repo_all("get_why (reference lookup)")
        return _stamp_answer_basis(await _why_reference(id, repo, reference=reference))

    # --- repo="all": search decisions across ALL repos ---
    if repo == "all":
        if not query:
            response = _unsupported_repo_all("get_why (health dashboard)")
            response["error"] += " To search all repositories, pass a query."
            return response
        return _stamp_answer_basis(_lean(await _why_workspace_search(query)))

    # --- Mode 1: No query → the targets, or the health dashboard ---
    # Targets first: naming files and asking nothing asks about those files.
    if not query:
        if targets:
            return _stamp_answer_basis(
                _lean(await _why_targets(list(targets), repo, history=history))
            )
        # An orientation call, not an answer: no basis to name.
        return _lean(await _why_health_dashboard(repo))
    return None


def _lean(result: dict) -> dict:
    # A workspace ref names its repository, so passing it back routes the lookup.
    return lean_response(result, keep_repository=_is_workspace_mode())


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

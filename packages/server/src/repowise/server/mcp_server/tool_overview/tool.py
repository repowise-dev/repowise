"""The get_overview tool: assembles the single-repo overview from its blocks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GitMetadata
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    filter_rows_by_attr,
)
from repowise.server.mcp_server._meta import (
    build_meta_with_full_scope as _build_meta_with_full_scope,
)
from repowise.server.mcp_server.tool_overview.decisions import _build_key_decisions
from repowise.server.mcp_server.tool_overview.graph import (
    _build_architecture,
    _build_community_summary,
    _load_community_nodes,
)
from repowise.server.mcp_server.tool_overview.health import _build_code_health
from repowise.server.mcp_server.tool_overview.history import (
    _build_git_health,
    _build_knowledge_map,
)
from repowise.server.mcp_server.tool_overview.outline import _build_outline, _load_tree_rows
from repowise.server.mcp_server.tool_overview.pages import (
    _capped_entry_points,
    _load_module_pages,
    _load_overview_page,
    _overview_content,
    _resolve_entry_point_ids,
    _resolve_title,
)
from repowise.server.mcp_server.tool_overview.surface import _tool_surface_guide
from repowise.server.mcp_server.tool_overview.tour import (
    _build_guided_tour,
    _build_reading_order,
)
from repowise.server.mcp_server.tool_overview.workspace import (
    _build_workspace_footer,
    _workspace_overview,
)


@mcp.tool(surface_order=80, artifact_type="overview", presentation="overview")
async def get_overview(repo: str | None = None, include: list[str] | None = None) -> dict:
    """Architecture map for an unfamiliar repo — first call when you don't know your way around.

    Returns the synthesised overview summary, key modules, entry points,
    architecture layers, code health, and repo-wide git health (hotspot count,
    churn trend, bus-factor distribution).
    Skip this on subsequent calls — once you have the map, jump straight to
    ``get_context`` / ``get_answer``.

    Compact by default: ``content_md`` carries only the overview essay's summary
    section, and the outline, onboarding, ownership and graph blocks ship only
    on request. The response's ``more`` field names them.

    Defaults fit 24,000 chars; nonempty ``include`` uses 32,000. Reductions
    carry counts and recovery status in ``_meta``.
    Include-gated blocks are projections, not omissions.

    In workspace mode:
    - Omit ``repo`` for the default repo's overview plus a workspace footer.
    - ``repo="all"`` returns the cross-repo topology (co-changes, package deps,
      API contracts) — no single-repo detail.
    - ``repo="<alias>"`` targets one specific repo.

    Args:
        repo: Repository alias, path, or ID. Use ``"all"`` for workspace overview.
        include: Opt-in extras, any combination of:
            ``"content"`` — the full overview essay instead of its summary.
            ``"outline"`` — the stored wiki page tree, two rungs deep.
            ``"tour"`` — ``guided_tour`` + ``reading_order`` onboarding walks.
            ``"decisions"`` — ``key_decisions``; ``get_why`` is richer.
            ``"graph"`` — ``community_summary``, code-community clusters.
            ``"ownership"`` — ``knowledge_map``: top owners, knowledge silos.
    """
    if repo == "all":
        return await _workspace_overview()

    ctx = await _resolve_repo_context(repo)
    exclude_spec = _get_exclude_spec(ctx.path)
    # Entries beyond the response caps below are persisted, not silently
    # dropped — the response carries an expandable [repowise#<ref>] marker.
    collector = OmissionCollector("get_overview", repo_root=ctx.path)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        result = await _repo_overview(
            session, repository, exclude_spec, set(include or []), collector
        )

        # The orientation call, and the one place the whole scope is worth its
        # bytes: it is made once per session and it is what the compact
        # projection on every other response points at.
        result["_meta"] = _build_meta_with_full_scope(repository=repository)
        collector.attach(result)
        return result


async def _repo_overview(
    session: Any,
    repository: Any,
    exclude_spec: Any,
    want: set[str],
    collector: OmissionCollector,
) -> dict[str, Any]:
    """Every single-repo block, in payload order; empty optional blocks are left out."""
    overview_page = await _load_overview_page(session, repository)
    module_pages = await _load_module_pages(session, repository, collector)
    entry_point_ids = await _resolve_entry_point_ids(session, repository, exclude_spec)
    all_git = await _load_git_rows(session, repository, exclude_spec)
    architecture = await _build_architecture(session, repository)
    code_health = await _build_code_health(session, repository)
    requested = await _requested_blocks(session, repository, exclude_spec, all_git, want)
    sections, outline = await _load_outline(session, repository, want, collector)
    content_md, content_hint = _overview_content(overview_page, "content" in want)

    result: dict[str, Any] = {
        "title": _resolve_title(overview_page, repository),
        "content_md": content_md,
        "code_health": code_health,
        # Names and paths only. get_context(path) carries the prose, and
        # section indexes into include=["outline"].
        "key_modules": [
            {"name": p.title, "path": p.target_path, "section": p.section_number}
            for p in module_pages
        ],
        "entry_points": _capped_entry_points(entry_point_ids, collector),
        "git_health": _build_git_health(all_git),
        "more": (
            'get_overview(include=[...]) also serves: "outline" (the wiki '
            'page tree), "tour" (guided_tour + reading_order), "decisions", '
            '"graph" (code communities), "ownership" (top owners, silos), '
            '"content" (the full essay).'
        ),
    }
    optional = {
        "knowledge_map": requested["knowledge_map"],
        "community_summary": requested["community_summary"],
        "content_hint": content_hint,
        "outline": outline,
        "outline_hint": _OUTLINE_HINT if outline else None,
        "architecture": architecture,
        "key_decisions": requested["key_decisions"],
        "reading_order": requested["reading_order"],
        "reading_order_hint": _READING_ORDER_HINT if requested["reading_order"] else None,
    }
    result.update((key, block) for key, block in optional.items() if block)

    # Topology-driven guided tour — the ordered, page-by-page walk derived
    # from the import graph (entry points first, then inward, infra last).
    # Persisted on the repo_overview page metadata at generation time.
    if overview_page:
        _build_guided_tour(overview_page, result, sections, "tour" in want)

    # Append workspace context footer when in workspace mode
    ws_footer = _build_workspace_footer()
    if ws_footer:
        result["workspace"] = ws_footer

    result["tool_surface"] = _tool_surface_guide(is_workspace=_state._registry is not None)
    return result


_OUTLINE_HINT = (
    "The stored page tree — the same outline the web app and the "
    "editor extension render. Every 'section' in this response "
    "indexes into it, and 'descendants' is how much sits below an "
    "entry. Two rungs deep; get_context on an entry's target_path "
    "to read it."
)

_READING_ORDER_HINT = (
    "Canonical onboarding sequence — read these page_ids in order "
    "via get_context/get_symbol to understand the repo the way a "
    "new contributor would."
)


async def _load_git_rows(session: Any, repository: Any, exclude_spec: Any) -> list[Any]:
    """Git metadata rows outside the exclude rules, the source of git health and ownership."""
    git_res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository.id,
        )
    )
    return filter_rows_by_attr(list(git_res.scalars().all()), "file_path", exclude_spec)


async def _requested_blocks(
    session: Any, repository: Any, exclude_spec: Any, all_git: list, want: set[str]
) -> dict[str, Any]:
    """Gated blocks: not built at all unless asked for."""
    community_summary: list[dict[str, Any]] = []
    if "graph" in want:
        all_nodes = await _load_community_nodes(session, repository, exclude_spec, all_git)
        community_summary = _build_community_summary(all_nodes)
    return {
        "knowledge_map": _build_knowledge_map(all_git) if "ownership" in want else {},
        "community_summary": community_summary,
        "reading_order": (
            await _build_reading_order(session, repository) if "tour" in want else []
        ),
        "key_decisions": (
            await _build_key_decisions(session, repository, exclude_spec)
            if "decisions" in want
            else []
        ),
    }


async def _load_outline(
    session: Any, repository: Any, want: set[str], collector: OmissionCollector
) -> tuple[dict[str, str | None], dict[str, Any]]:
    """Section labels by page id, and the outline when it was asked for.

    The tree backs the outline and the tour's section labels.
    """
    if not want & {"outline", "tour"}:
        return {}, {}
    tree_rows = await _load_tree_rows(session, repository)
    sections = {r.id: r.section_number for r in tree_rows}
    outline = _build_outline(tree_rows, 2, collector) if "outline" in want else {}
    return sections, outline

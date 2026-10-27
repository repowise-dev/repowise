"""Workspace views: the repo="all" summary and the footer on a default-repo overview."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select

from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.persistence.crud import count_symbols
from repowise.core.persistence.crud.analysis.health.metrics import count_health_files
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._federation import Federation, sum_fields
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_overview.pages import _load_overview_page
from repowise.server.mcp_server.tool_overview.surface import _tool_surface_guide


async def _workspace_overview(*, tools: bool = False) -> dict:
    """Build a concise workspace-level overview across all repos."""
    registry = _state._registry
    fed = Federation()
    repos_info = [entry for _, entry in await fed.run(lambda ctx: _repo_entry(ctx, registry))]
    cross_repo_topology = _cross_repo_topology(repos_info)
    totals = sum_fields(repos_info, ("code_files", "symbol_count"))

    result: dict[str, Any] = {
        "workspace": True,
        "workspace_root": str(registry.workspace_root) if registry else "",
        "total_repos": len(repos_info),
        "total_code_files": totals["code_files"],
        "total_symbols": totals["symbol_count"],
        "repos": repos_info,
        "hint": ("Use repo='<alias>' to query a specific repo. Omit repo to use the default."),
        "_meta": _build_meta(),
    }
    if tools:
        result["tool_surface"] = _tool_surface_guide(is_workspace=True)
    if cross_repo_topology:
        result["cross_repo_topology"] = cross_repo_topology

    collector = OmissionCollector(
        "get_overview", repo_root=registry.workspace_root if registry else None
    )
    collector.attach(result)
    return fed.annotate(result, repos_info, repo_key="alias")


async def _repo_entry(ctx: Any, registry: Any) -> dict[str, Any]:
    """One repo's line in the workspace summary: overview headline and graph size."""
    async with get_session(ctx.session_factory) as session:
        repo_obj = await _get_repo(session)
        # Same multi-row safety as the single-repo overview.
        overview_page = await _load_overview_page(session, repo_obj)
        ov_content = (overview_page.content if overview_page else None) or ""
        return {
            "alias": ctx.alias,
            "path": str(ctx.path),
            "summary": _lead_sentence(ov_content),
            "code_files": await _code_files(session, repo_obj.id),
            "symbol_count": await count_symbols(session, repo_obj.id),
            "is_default": registry is not None and ctx.alias == registry.get_default_alias(),
        }


def _is_prose(line: str) -> bool:
    """Not blank, a heading, a table row, a list item, a quote or a code fence."""
    text = line.strip()
    return bool(text) and not text.startswith(("#", "|", "- ", "* ", "```", ">"))


def _lead_sentence(markdown: str, limit: int = 140) -> str:
    """The essay's first sentence of prose, past its headings."""
    prose = " ".join(
        line.strip() for line in markdown.splitlines() if _is_prose(line)
    )
    head, dot, _ = prose.partition(". ")
    sentence = head + "." if dot else prose
    if len(sentence) <= limit:
        return sentence
    return sentence[: limit - 1].rsplit(" ", 1)[0] + "…"


async def _code_files(session: Any, repository_id: str) -> int:
    """The single-repo overview's ``code_health.code_files``; before health has
    run, the graph's files in a code language."""
    counted = await count_health_files(session, repository_id)
    if counted:
        return counted
    result = await session.execute(
        select(sa_func.count())
        .select_from(GraphNode)
        .where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "file",
            GraphNode.language.in_(REGISTRY.code_languages()),
        )
    )
    return result.scalar_one()


def _cross_repo_topology(repos_info: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-repo summary and contracts; also tags each repo with the repos it depends on."""
    enricher = _enricher_with_data()
    if enricher is None:
        return {}
    topology = enricher.get_cross_repo_summary()
    if enricher.has_contract_data:
        topology["contracts"] = enricher.get_contract_summary()
    for repo_info in repos_info:
        deps = enricher.get_package_deps(repo_info["alias"])
        if deps:
            repo_info["depends_on"] = sorted(set(d["target_repo"] for d in deps))
    return topology


def _enricher_with_data() -> Any | None:
    enricher = _state._cross_repo_enricher
    return enricher if enricher is not None and enricher.has_data else None


def _build_workspace_footer() -> dict | None:
    """Build workspace context footer for the default overview."""
    registry = _state._registry
    if registry is None:
        return None

    default_alias = registry.get_default_alias()
    other_repos = [a for a in registry.get_all_aliases() if a != default_alias]
    if not other_repos:
        return None

    # Names only: the cross-repo links are what repo="all" serves, so the
    # footer points there rather than restating them.
    return {
        "workspace_root": str(registry.workspace_root),
        "default_repo": default_alias,
        "other_repos": other_repos,
        "hint": (
            "Use repo='<alias>' for another repo, "
            "or repo='all' for cross-repo links and contracts."
        ),
    }

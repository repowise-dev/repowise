"""MCP Tool 2: get_context — complete context for files, modules, or symbols."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._server import mcp


async def _resolve_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    include: set[str] | None,
) -> dict:
    """Resolve a single target and return its full context."""
    repo_id = repository.id
    result_data: dict[str, Any] = {}

    # --- Determine target type ---
    # 1. Try file page (most common)
    page_id = f"file_page:{target}"
    page = await session.get(Page, page_id)
    target_type = None
    file_path_for_git: str | None = None

    if page and page.repository_id == repo_id:
        target_type = "file"
        file_path_for_git = target
    else:
        # 2. Try module page
        res = await session.execute(
            select(Page).where(
                Page.repository_id == repo_id,
                Page.page_type == "module_page",
                Page.target_path == target,
            )
        )
        page = res.scalar_one_or_none()
        if page is None:
            # Partial match fallback for modules
            res = await session.execute(
                select(Page).where(
                    Page.repository_id == repo_id,
                    Page.page_type == "module_page",
                    Page.target_path.contains(target),
                )
            )
            page = res.scalar_one_or_none()
        if page:
            target_type = "module"
        else:
            # 3. Try symbol (exact then fuzzy)
            res = await session.execute(
                select(WikiSymbol).where(
                    WikiSymbol.repository_id == repo_id,
                    WikiSymbol.name == target,
                )
            )
            sym_matches = list(res.scalars().all())
            if not sym_matches:
                res = await session.execute(
                    select(WikiSymbol)
                    .where(
                        WikiSymbol.repository_id == repo_id,
                        WikiSymbol.name.ilike(f"%{target}%"),
                    )
                    .limit(10)
                )
                sym_matches = list(res.scalars().all())
            if sym_matches:
                target_type = "symbol"
                file_path_for_git = sym_matches[0].file_path
            else:
                # 4. Try file page by target_path search
                res = await session.execute(
                    select(Page).where(
                        Page.repository_id == repo_id,
                        Page.page_type == "file_page",
                        Page.target_path == target,
                    )
                )
                page = res.scalar_one_or_none()
                if page:
                    target_type = "file"
                    file_path_for_git = target

    if target_type is None:
        # F1: check git_metadata — file may exist but have no wiki page
        res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path == target,
            )
        )
        meta = res.scalar_one_or_none()
        if meta:
            return {
                "target": target,
                "error": (
                    f"'{target}' exists in the repository but has no wiki page. "
                    "This usually means the file has too few symbols or is below "
                    "the PageRank threshold. Run `repowise update` to regenerate docs."
                ),
                "exists_in_git": True,
                "last_commit_at": meta.last_commit_at.isoformat() if meta.last_commit_at else None,
                "primary_owner": meta.primary_owner_name,
                "is_hotspot": meta.is_hotspot,
            }

        # F5: fuzzy path suggestions — match by filename or partial path
        tail = target.rsplit("/", 1)[-1]
        res = await session.execute(
            select(GitMetadata.file_path)
            .where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path.contains(tail),
            )
            .limit(5)
        )
        suggestions = [row[0] for row in res.all() if row[0] != target]
        if suggestions:
            return {
                "target": target,
                "error": f"Target not found: '{target}'",
                "suggestions": suggestions,
            }

        return {"target": target, "error": f"Target not found: '{target}'"}

    result_data["target"] = target
    result_data["type"] = target_type

    # --- Docs ---
    if include is None or "docs" in include:
        docs: dict[str, Any] = {}
        if target_type == "file":
            docs["title"] = page.title
            docs["content_md"] = page.content
            # Symbols in this file
            res = await session.execute(
                select(WikiSymbol).where(
                    WikiSymbol.repository_id == repo_id,
                    WikiSymbol.file_path == target,
                )
            )
            symbols = res.scalars().all()
            docs["symbols"] = [
                {"name": s.name, "kind": s.kind, "signature": s.signature} for s in symbols
            ]
            # Importers
            res = await session.execute(
                select(GraphEdge).where(
                    GraphEdge.repository_id == repo_id,
                    GraphEdge.target_node_id == target,
                )
            )
            importers = res.scalars().all()
            docs["imported_by"] = [e.source_node_id for e in importers]

        elif target_type == "module":
            docs["title"] = page.title
            docs["content_md"] = page.content
            # Child file pages
            res = await session.execute(
                select(Page).where(
                    Page.repository_id == repo_id,
                    Page.page_type == "file_page",
                    Page.target_path.like(f"{page.target_path}/%"),
                )
            )
            file_pages = res.scalars().all()
            docs["files"] = [
                {
                    "path": f.target_path,
                    "description": f.title,
                    "confidence_score": f.confidence,
                }
                for f in file_pages
            ]

        elif target_type == "symbol":
            sym = sym_matches[0]  # type: ignore[possibly-undefined]
            docs["name"] = sym.name
            docs["qualified_name"] = sym.qualified_name
            docs["kind"] = sym.kind
            docs["signature"] = sym.signature
            docs["file_path"] = sym.file_path
            docs["docstring"] = sym.docstring or ""
            # File page content as documentation
            sym_page_id = f"file_page:{sym.file_path}"
            sym_page = await session.get(Page, sym_page_id)
            docs["documentation"] = sym_page.content if sym_page else ""
            # Used by
            res = await session.execute(
                select(GraphEdge).where(
                    GraphEdge.repository_id == repo_id,
                    GraphEdge.target_node_id == sym.file_path,
                )
            )
            edges = res.scalars().all()
            docs["used_by"] = [e.source_node_id for e in edges][:20]
            # Candidates
            if len(sym_matches) > 1:  # type: ignore[possibly-undefined]
                docs["candidates"] = [
                    {"name": m.name, "kind": m.kind, "file_path": m.file_path}
                    for m in sym_matches[1:5]  # type: ignore[possibly-undefined]
                ]

        result_data["docs"] = docs

    # --- Ownership ---
    if include is None or "ownership" in include:
        ownership: dict[str, Any] = {}
        git_path = file_path_for_git
        if target_type == "module" and page:
            git_path = page.target_path
        if git_path:
            res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == git_path,
                )
            )
            meta = res.scalar_one_or_none()
            if meta:
                ownership["primary_owner"] = meta.primary_owner_name
                ownership["owner_pct"] = meta.primary_owner_commit_pct
                ownership["contributor_count"] = getattr(meta, "contributor_count", 0) or len(
                    json.loads(meta.top_authors_json)
                )
                ownership["bus_factor"] = getattr(meta, "bus_factor", 0) or 0
                # Recent owner (who maintains this file now)
                recent = getattr(meta, "recent_owner_name", None)
                if recent and recent != meta.primary_owner_name:
                    ownership["recent_owner"] = recent
                    ownership["recent_owner_pct"] = getattr(meta, "recent_owner_commit_pct", None)
            else:
                ownership["primary_owner"] = None
                ownership["owner_pct"] = None
                ownership["contributor_count"] = 0
                ownership["bus_factor"] = 0
        else:
            ownership["primary_owner"] = None
            ownership["owner_pct"] = None
            ownership["contributor_count"] = 0
            ownership["bus_factor"] = 0
        result_data["ownership"] = ownership

    # --- Last change ---
    if include is None or "last_change" in include:
        last_change: dict[str, Any] = {}
        git_path = file_path_for_git
        if target_type == "module" and page:
            git_path = page.target_path
        if git_path:
            res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == git_path,
                )
            )
            meta = res.scalar_one_or_none()
            if meta:
                last_change["date"] = (
                    meta.last_commit_at.isoformat() if meta.last_commit_at else None
                )
                last_change["author"] = meta.primary_owner_name
                last_change["days_ago"] = meta.age_days
            else:
                last_change["date"] = None
                last_change["author"] = None
                last_change["days_ago"] = None
        else:
            last_change["date"] = None
            last_change["author"] = None
            last_change["days_ago"] = None
        result_data["last_change"] = last_change

    # --- Decisions ---
    if include is None or "decisions" in include:
        res = await session.execute(
            select(DecisionRecord).where(
                DecisionRecord.repository_id == repo_id,
            )
        )
        all_decisions = res.scalars().all()
        governing = []
        for d in all_decisions:
            affected_files = json.loads(d.affected_files_json)
            affected_modules = json.loads(d.affected_modules_json)
            if (
                target in affected_files
                or target in affected_modules
                or (file_path_for_git and file_path_for_git in affected_files)
            ):
                governing.append(
                    {
                        "id": d.id,
                        "title": d.title,
                        "status": d.status,
                        "decision": d.decision,
                        "rationale": d.rationale,
                        "confidence": d.confidence,
                    }
                )
        result_data["decisions"] = governing

    # --- Freshness ---
    if include is None or "freshness" in include:
        freshness: dict[str, Any] = {}
        if page:
            freshness["confidence_score"] = page.confidence
            freshness["freshness_status"] = page.freshness_status
            freshness["is_stale"] = (page.confidence or 1.0) < 0.6
        elif target_type == "symbol" and file_path_for_git:
            sym_page_id = f"file_page:{file_path_for_git}"
            sym_page = await session.get(Page, sym_page_id)
            if sym_page:
                freshness["confidence_score"] = sym_page.confidence
                freshness["freshness_status"] = sym_page.freshness_status
                freshness["is_stale"] = (sym_page.confidence or 1.0) < 0.6
            else:
                freshness["confidence_score"] = None
                freshness["freshness_status"] = None
                freshness["is_stale"] = None
        else:
            freshness["confidence_score"] = None
            freshness["freshness_status"] = None
            freshness["is_stale"] = None
        result_data["freshness"] = freshness

    return result_data


@mcp.tool()
async def get_context(
    targets: list[str],
    include: list[str] | None = None,
    repo: str | None = None,
) -> dict:
    """Get complete context for one or more targets (files, modules, or symbols).

    Pass ALL relevant targets in a single call rather than calling this tool
    multiple times. Each target is resolved automatically — pass file paths
    like "src/auth/service.py", module paths like "src/auth", or symbol names
    like "AuthService".

    Example: get_context(["src/auth/service.py", "src/auth/middleware.py", "AuthService"])

    Optional `include` parameter filters response fields:
    ["docs", "ownership", "last_change", "decisions", "freshness"]
    Default: all fields returned.

    Args:
        targets: List of file paths, module paths, or symbol names to look up.
        include: Optional list of fields to include. Default returns all.
        repo: Repository path, name, or ID.
    """
    include_set = set(include) if include else None

    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)

        results = await asyncio.gather(
            *[_resolve_one_target(session, repository, t, include_set) for t in targets]
        )

    return {
        "targets": {r["target"]: r for r in results},
    }

"""repowise MCP Server — 8 tools for AI coding assistants.

Exposes the full repowise wiki as queryable tools via the MCP protocol.
Supports both stdio transport (Claude Code, Cursor, Cline) and SSE transport
(web-based MCP clients).

Usage:
    repowise mcp --transport stdio  # for Claude Code / Cursor / Cline
    repowise mcp --transport sse    # for web-based clients
"""

from __future__ import annotations

import asyncio
import json
import os
import os.path
import re
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence.database import get_session, init_db
from repowise.core.persistence.embedder import MockEmbedder
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore

# ---------------------------------------------------------------------------
# Shared state (set during lifespan)
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker[AsyncSession] | None = None
_vector_store: Any = None
_decision_store: Any = None
_fts: Any = None
_repo_path: str | None = None


def _sanitize_mermaid_id(node_id: str) -> str:
    """Replace all non-alphanumeric/non-underscore chars with underscore."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", node_id)


def _resolve_embedder():
    """Resolve embedder from REPOWISE_EMBEDDER env var or .repowise/config.yaml."""
    name = os.environ.get("REPOWISE_EMBEDDER", "").lower()
    if not name and _repo_path:
        try:
            from pathlib import Path

            cfg_path = Path(_repo_path) / ".repowise" / "config.yaml"
            if cfg_path.exists():
                import yaml

                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                name = (cfg.get("embedder") or "").lower()
        except Exception:
            pass
    if name == "gemini":
        try:
            from repowise.core.persistence.gemini_embedder import GeminiEmbedder

            dims = int(os.environ.get("REPOWISE_EMBEDDING_DIMS", "768"))
            return GeminiEmbedder(output_dimensionality=dims)
        except Exception:
            pass
    if name == "openai":
        try:
            from repowise.core.persistence.openai_embedder import OpenAIEmbedder

            model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "text-embedding-3-small")
            return OpenAIEmbedder(model=model)
        except Exception:
            pass
    return MockEmbedder()


@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialize DB engine, session factory, vector store, FTS on startup."""
    global _session_factory, _vector_store, _decision_store, _fts  # noqa: PLW0603

    db_url = os.environ.get(
        "REPOWISE_DATABASE_URL", "sqlite+aiosqlite:///repowise.db"
    )

    # If a repo path was configured, try .repowise/wiki.db
    if _repo_path:
        from pathlib import Path
        import logging as _logging

        _log = _logging.getLogger("repowise.mcp")
        repowise_dir = Path(_repo_path) / ".repowise"
        if not repowise_dir.exists():
            _log.warning(
                "No .repowise directory at %s — run 'repowise init' first",
                _repo_path,
            )
        elif not (repowise_dir / "wiki.db").exists():
            _log.warning(
                "No wiki.db in %s — run 'repowise init' to generate the wiki",
                repowise_dir,
            )
        if repowise_dir.exists():
            db_path = repowise_dir / "wiki.db"
            if db_path.exists():
                db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_async_engine(db_url, connect_args=connect_args)
    await init_db(engine)

    _session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    _fts = FullTextSearch(engine)
    await _fts.ensure_index()

    # Resolve real embedder from env/config instead of always using MockEmbedder
    embedder = _resolve_embedder()
    _vector_store = InMemoryVectorStore(embedder=embedder)

    # Try to load LanceDB if available
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore

        if _repo_path:
            from pathlib import Path

            lance_dir = Path(_repo_path) / ".repowise" / "lancedb"
            if lance_dir.exists():
                _vector_store = LanceDBVectorStore(
                    str(lance_dir), embedder=embedder
                )
                _decision_store = LanceDBVectorStore(
                    str(lance_dir), embedder=embedder, table_name="decision_records"
                )
    except ImportError:
        pass

    if _decision_store is None:
        _decision_store = InMemoryVectorStore(embedder=embedder)

    yield

    await engine.dispose()
    await _vector_store.close()
    if _decision_store is not None:
        await _decision_store.close()


# ---------------------------------------------------------------------------
# Create the MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "repowise",
    instructions=(
        "repowise is a codebase documentation engine. Use these tools to query "
        "the wiki for architecture overviews, contextual docs on files/modules/"
        "symbols, modification risk assessment, architectural decision rationale, "
        "semantic search, dependency paths, dead code, and architecture diagrams."
    ),
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CODE_EXTS = frozenset({
    ".py", ".ts", ".js", ".go", ".rs", ".java", ".tsx", ".jsx",
    ".rb", ".kt", ".cpp", ".c", ".h", ".cs", ".swift", ".scala",
})


async def _get_repo(session: AsyncSession, repo: str | None = None) -> Repository:
    """Resolve a repository — by path, by ID, or return the first one."""
    if repo:
        # Try by path
        result = await session.execute(
            select(Repository).where(Repository.local_path == repo)
        )
        obj = result.scalar_one_or_none()
        if obj:
            return obj
        # Try by ID
        obj = await session.get(Repository, repo)
        if obj:
            return obj
        # Try by name
        result = await session.execute(
            select(Repository).where(Repository.name == repo)
        )
        obj = result.scalar_one_or_none()
        if obj:
            return obj
        raise LookupError(f"Repository not found: {repo}")

    # Default: return the first (and often only) repository
    result = await session.execute(select(Repository).limit(1))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise LookupError(
            "No repositories found. Run 'repowise init' first."
        )
    return obj


def _is_path(query: str) -> bool:
    """Heuristic: does this string look like a file or module path?"""
    if "/" in query:
        return True
    _, ext = os.path.splitext(query)
    return ext in _CODE_EXTS


def _build_origin_story(
    file_path: str, git_meta: Any | None, governing_decisions: list[dict],
) -> dict:
    """Build the human context / origin story for a file from stored metadata."""
    if git_meta is None:
        return {
            "available": False,
            "summary": f"No git history available for {file_path}.",
        }

    authors = json.loads(git_meta.top_authors_json) if git_meta.top_authors_json else []
    commits = json.loads(git_meta.significant_commits_json) if git_meta.significant_commits_json else []

    # Find the earliest significant commit as the "creation" context
    earliest_commit = None
    if commits:
        sorted_commits = sorted(commits, key=lambda c: c.get("date", ""))
        earliest_commit = sorted_commits[0]

    # Link commits to decisions via keyword overlap
    linked_decisions = []
    for d in governing_decisions:
        # Build a keyword set from the decision
        decision_text = f"{d.get('title', '')} {d.get('decision', '')} {d.get('rationale', '')}".lower()
        decision_words = set(decision_text.split())
        decision_words -= {"the", "a", "an", "is", "for", "to", "of", "in", "and", "or", "with"}

        # Find commits whose messages overlap with this decision
        related_commits = []
        for c in commits:
            msg = c.get("message", "").lower()
            msg_words = set(msg.split())
            msg_words -= {"the", "a", "an", "is", "for", "to", "of", "in", "and", "or", "with"}
            overlap = decision_words & msg_words
            # Require at least 1 meaningful word match
            if len(overlap) >= 1:
                related_commits.append({
                    "sha": c.get("sha", ""),
                    "message": c.get("message", ""),
                    "author": c.get("author", ""),
                    "date": c.get("date", ""),
                    "matching_keywords": sorted(overlap)[:5],
                })

        linked_decisions.append({
            "title": d.get("title", ""),
            "status": d.get("status", ""),
            "source": d.get("source", ""),
            "rationale": d.get("rationale", ""),
            "evidence_commits": related_commits,
        })

    # Build narrative summary
    primary = git_meta.primary_owner_name or "unknown"
    total = git_meta.commit_count_total or 0
    first_date = git_meta.first_commit_at.strftime("%Y-%m-%d") if git_meta.first_commit_at else "unknown"
    last_date = git_meta.last_commit_at.strftime("%Y-%m-%d") if git_meta.last_commit_at else "unknown"
    age = git_meta.age_days or 0

    parts = [f"Created ~{first_date}, last modified {last_date} ({age} days old)."]
    parts.append(f"Primary author: {primary} ({total} total commits).")

    if earliest_commit:
        parts.append(
            f"Earliest key commit: \"{earliest_commit.get('message', '')}\" "
            f"by {earliest_commit.get('author', 'unknown')} on {earliest_commit.get('date', 'unknown')}."
        )

    if linked_decisions:
        decision_titles = [d["title"] for d in linked_decisions[:3]]
        parts.append(f"Governed by: {', '.join(decision_titles)}.")
        # Highlight any commit-decision links
        for ld in linked_decisions:
            if ld["evidence_commits"]:
                ec = ld["evidence_commits"][0]
                parts.append(
                    f"Commit \"{ec['message']}\" by {ec['author']} "
                    f"is evidence for \"{ld['title']}\"."
                )

    contributor_count = len(authors)
    if contributor_count > 1:
        names = [a.get("name", "") for a in authors[:3]]
        parts.append(f"Contributors: {', '.join(names)}.")

    return {
        "available": True,
        "primary_author": primary,
        "author_commit_pct": git_meta.primary_owner_commit_pct,
        "contributors": authors[:5],
        "total_commits": total,
        "first_commit": first_date,
        "last_commit": last_date,
        "age_days": age,
        "key_commits": commits[:5],
        "linked_decisions": linked_decisions,
        "summary": " ".join(parts),
    }


def _compute_alignment(
    file_path: str, governing: list[dict], all_decisions: list,
) -> dict:
    """Compute how well a file aligns with established architectural decisions."""
    if not governing:
        return {
            "score": "none",
            "explanation": (
                f"No architectural decisions govern {file_path}. "
                "This file is ungoverned — it may be an outlier or simply undocumented."
            ),
            "governing_count": 0,
            "active_count": 0,
            "deprecated_count": 0,
            "stale_count": 0,
            "sibling_coverage": None,
        }

    # Count decision statuses
    active = [d for d in governing if d["status"] == "active"]
    deprecated = [d for d in governing if d["status"] in ("deprecated", "superseded")]
    stale = [d for d in governing if d.get("staleness_score", 0) > 0.5]
    proposed = [d for d in governing if d["status"] == "proposed"]

    # Check sibling files — do neighbors share the same decisions?
    dir_path = "/".join(file_path.split("/")[:-1])
    sibling_decision_ids = set()
    file_decision_titles = {d["title"] for d in governing}

    for d in all_decisions:
        affected = json.loads(d.affected_files_json)
        affected_modules = json.loads(d.affected_modules_json)
        for af in affected:
            af_dir = "/".join(af.split("/")[:-1])
            if af_dir == dir_path and af != file_path:
                sibling_decision_ids.add(d.title)

    # Overlap: how many of sibling decisions also cover this file
    if sibling_decision_ids:
        shared = file_decision_titles & sibling_decision_ids
        sibling_coverage = len(shared) / len(sibling_decision_ids)
    else:
        sibling_coverage = None  # No siblings to compare

    # Compute alignment score
    if deprecated and not active and not proposed:
        score = "low"
        explanation = (
            f"All governing decisions are deprecated/superseded. "
            f"This file likely contains technical debt that should be migrated."
        )
    elif stale and len(stale) >= len(governing) / 2:
        score = "low"
        explanation = (
            f"{len(stale)} of {len(governing)} governing decision(s) are stale. "
            f"The architectural rationale may no longer apply."
        )
    elif active:
        if sibling_coverage is not None and sibling_coverage >= 0.5:
            score = "high"
            explanation = (
                f"Follows {len(active)} active decision(s) shared with sibling files. "
                f"This file aligns with established patterns in {dir_path}/."
            )
        elif sibling_coverage is not None and sibling_coverage < 0.5:
            score = "medium"
            explanation = (
                f"Has {len(active)} active decision(s) but limited overlap with "
                f"sibling files in {dir_path}/. May use a different pattern than neighbors."
            )
        else:
            score = "high"
            explanation = f"Governed by {len(active)} active decision(s)."
    elif proposed:
        score = "medium"
        explanation = (
            f"Governed by {len(proposed)} proposed (unreviewed) decision(s). "
            f"Patterns are established but not yet formally approved."
        )
    else:
        score = "medium"
        explanation = f"Governed by {len(governing)} decision(s) with mixed status."

    return {
        "score": score,
        "explanation": explanation,
        "governing_count": len(governing),
        "active_count": len(active),
        "deprecated_count": len(deprecated),
        "stale_count": len(stale),
        "sibling_coverage": round(sibling_coverage, 2) if sibling_coverage is not None else None,
    }


# ---------------------------------------------------------------------------
# Tool 1: get_overview (unchanged)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_overview(repo: str | None = None) -> dict:
    """Get the repository overview: architecture summary, module map, key entry points.

    Best first call when starting to explore an unfamiliar codebase.

    Args:
        repo: Repository path, name, or ID. Omit if only one repo exists.
    """
    async with get_session(_session_factory) as session:
        repository = await _get_repo(session, repo)

        # Get repo overview page
        result = await session.execute(
            select(Page).where(
                Page.repository_id == repository.id,
                Page.page_type == "repo_overview",
            )
        )
        overview_page = result.scalar_one_or_none()

        # Get architecture diagram page
        result = await session.execute(
            select(Page).where(
                Page.repository_id == repository.id,
                Page.page_type == "architecture_diagram",
            )
        )
        arch_page = result.scalar_one_or_none()

        # Get module pages
        result = await session.execute(
            select(Page)
            .where(
                Page.repository_id == repository.id,
                Page.page_type == "module_page",
            )
            .order_by(Page.title)
        )
        module_pages = result.scalars().all()

        # Get entry point files from graph nodes
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.is_entry_point == True,  # noqa: E712
            )
        )
        entry_nodes = result.scalars().all()

        return {
            "title": overview_page.title if overview_page else repository.name,
            "content_md": overview_page.content if overview_page else "No overview generated yet.",
            "architecture_diagram_mermaid": arch_page.content if arch_page else None,
            "key_modules": [
                {
                    "name": p.title,
                    "path": p.target_path,
                    "description": (
                        p.content[:200].rsplit(" ", 1)[0] + "..."
                        if len(p.content) > 200
                        else p.content
                    ),
                }
                for p in module_pages
            ],
            "entry_points": [n.node_id for n in entry_nodes],
        }


# ---------------------------------------------------------------------------
# Tool 2: get_context (NEW — replaces 5 tools)
# ---------------------------------------------------------------------------


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
                    select(WikiSymbol).where(
                        WikiSymbol.repository_id == repo_id,
                        WikiSymbol.name.ilike(f"%{target}%"),
                    ).limit(10)
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
                {"name": s.name, "kind": s.kind, "signature": s.signature}
                for s in symbols
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
                ownership["contributor_count"] = getattr(meta, "contributor_count", 0) or len(json.loads(meta.top_authors_json))
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
                last_change["date"] = meta.last_commit_at.isoformat() if meta.last_commit_at else None
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
            if target in affected_files or target in affected_modules:
                governing.append({
                    "id": d.id,
                    "title": d.title,
                    "status": d.status,
                    "decision": d.decision,
                    "rationale": d.rationale,
                    "confidence": d.confidence,
                })
            elif file_path_for_git and file_path_for_git in affected_files:
                governing.append({
                    "id": d.id,
                    "title": d.title,
                    "status": d.status,
                    "decision": d.decision,
                    "rationale": d.rationale,
                    "confidence": d.confidence,
                })
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

    async with get_session(_session_factory) as session:
        repository = await _get_repo(session, repo)

        results = await asyncio.gather(*[
            _resolve_one_target(session, repository, t, include_set)
            for t in targets
        ])

    return {
        "targets": {r["target"]: r for r in results},
    }


# ---------------------------------------------------------------------------
# Tool 3: get_risk (NEW — replaces 3 tools)
# ---------------------------------------------------------------------------


async def _assess_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    all_edge_map: dict[str, int],
    import_links: dict[str, set[str]],
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
) -> dict:
    """Assess risk for a single target file."""
    repo_id = repository.id
    result_data: dict[str, Any] = {"target": target}

    dep_count = all_edge_map.get(target, 0)

    # Git metadata
    res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.file_path == target,
        )
    )
    meta = res.scalar_one_or_none()

    if meta is None:
        result_data["hotspot_score"] = 0.0
        result_data["dependents_count"] = dep_count
        result_data["co_change_partners"] = []
        result_data["primary_owner"] = None
        result_data["owner_pct"] = None
        result_data["trend"] = "unknown"
        result_data["risk_type"] = "high-coupling" if dep_count >= 5 else "unknown"
        result_data["impact_surface"] = _compute_impact_surface(
            target, reverse_deps, node_meta,
        )
        result_data["risk_summary"] = f"{target} — no git metadata available"
        return result_data

    hotspot_score = meta.churn_percentile or 0.0

    # Co-change partners
    partners = json.loads(meta.co_change_partners_json)
    import_related = import_links.get(target, set())
    co_changes = [
        {
            "file_path": p.get("file_path", p.get("path", "")),
            "count": p.get("co_change_count", p.get("count", 0)),
            "last_co_change": p.get("last_co_change"),
            "has_import_link": p.get("file_path", p.get("path", "")) in import_related,
        }
        for p in partners
    ]

    owner = meta.primary_owner_name or "unknown"
    pct = meta.primary_owner_commit_pct or 0.0

    # --- Risk velocity (trend) ---
    trend = _compute_trend(meta)

    # --- Risk type classification ---
    risk_type = _classify_risk_type(meta, dep_count)

    # --- Impact surface ---
    impact_surface = _compute_impact_surface(target, reverse_deps, node_meta)

    # Phase 2: diff size & change magnitude
    lines_added = getattr(meta, "lines_added_90d", 0) or 0
    lines_deleted = getattr(meta, "lines_deleted_90d", 0) or 0
    avg_size = getattr(meta, "avg_commit_size", 0.0) or 0.0

    # Phase 2: commit classification → change_pattern
    categories = {}
    cat_json = getattr(meta, "commit_categories_json", None)
    if cat_json:
        try:
            categories = json.loads(cat_json)
        except (json.JSONDecodeError, TypeError):
            pass
    change_pattern = _derive_change_pattern(categories)

    # Phase 2: recent owner & bus factor
    recent_owner = getattr(meta, "recent_owner_name", None)
    recent_owner_pct = getattr(meta, "recent_owner_commit_pct", None)
    bus_factor = getattr(meta, "bus_factor", 0) or 0
    contributor_count = getattr(meta, "contributor_count", 0) or 0

    # Phase 3: rename tracking & merge commit proxy
    original_path = getattr(meta, "original_path", None)
    merge_commit_count = getattr(meta, "merge_commit_count_90d", 0) or 0

    result_data["hotspot_score"] = hotspot_score
    result_data["dependents_count"] = dep_count
    result_data["co_change_partners"] = co_changes
    result_data["primary_owner"] = owner
    result_data["owner_pct"] = pct
    result_data["recent_owner"] = recent_owner
    result_data["recent_owner_pct"] = recent_owner_pct
    result_data["bus_factor"] = bus_factor
    result_data["contributor_count"] = contributor_count
    result_data["trend"] = trend
    result_data["risk_type"] = risk_type
    result_data["change_pattern"] = change_pattern
    result_data["change_magnitude"] = {
        "lines_added_90d": lines_added,
        "lines_deleted_90d": lines_deleted,
        "avg_commit_size": round(avg_size, 1),
    }
    result_data["impact_surface"] = impact_surface
    if original_path:
        result_data["original_path"] = original_path
    if merge_commit_count > 0:
        result_data["merge_commit_count_90d"] = merge_commit_count

    capped = getattr(meta, "commit_count_capped", False)
    capped_note = " (history truncated — actual count may be higher)" if capped else ""
    result_data["commit_count_capped"] = capped

    bus_note = ""
    if bus_factor == 1 and (meta.commit_count_total or 0) > 20:
        bus_note = f", bus factor risk (sole maintainer: {owner})"

    result_data["risk_summary"] = (
        f"{target} — hotspot score {hotspot_score:.0%} ({trend}), "
        f"{dep_count} dependents, {risk_type}, {change_pattern}, "
        f"{len(co_changes)} co-change partners, owned {pct:.0%} by {owner}"
        f"{bus_note}{capped_note}"
    )

    return result_data


_FIX_PATTERN = re.compile(
    r"\b(fix|bug|patch|hotfix|revert|regression|broken|crash|error)\b",
    re.IGNORECASE,
)


def _derive_change_pattern(categories: dict[str, int]) -> str:
    """Derive a human-readable change pattern from commit category counts."""
    if not categories:
        return "uncategorized"
    total = sum(categories.values())
    if total == 0:
        return "uncategorized"
    dominant = max(categories, key=lambda k: categories[k])
    ratio = categories[dominant] / total
    if ratio >= 0.5:
        labels = {
            "feature": "feature-active",
            "refactor": "primarily refactored",
            "fix": "bug-prone",
            "dependency": "dependency-churn",
        }
        return labels.get(dominant, dominant)
    return "mixed-activity"


def _compute_trend(meta: Any) -> str:
    """Compute risk velocity from 30d vs 90d commit rates."""
    c30 = meta.commit_count_30d or 0
    c90 = meta.commit_count_90d or 0
    # Baseline: commits in the 60-day window before the last 30 days
    baseline_commits = c90 - c30
    recent_rate = c30 / 30.0
    baseline_rate = baseline_commits / 60.0

    if c90 == 0:
        return "stable"
    if baseline_rate == 0:
        return "increasing" if c30 > 0 else "stable"
    ratio = recent_rate / baseline_rate
    if ratio > 1.5:
        return "increasing"
    elif ratio < 0.5:
        return "decreasing"
    return "stable"


def _classify_risk_type(meta: Any, dep_count: int) -> str:
    """Classify risk as churn-heavy, bug-prone, high-coupling, or bus-factor-risk."""
    # Count bug-fix commits from significant_commits messages
    commits = json.loads(meta.significant_commits_json) if meta.significant_commits_json else []
    fix_count = sum(
        1 for c in commits
        if _FIX_PATTERN.search(c.get("message", ""))
    )

    churn_score = meta.churn_percentile or 0.0
    bus_factor = getattr(meta, "bus_factor", 0) or 0
    total_commits = meta.commit_count_total or 0

    # Bug-prone takes priority if fix ratio is high
    if commits and fix_count / len(commits) >= 0.4:
        return "bug-prone"
    if churn_score >= 0.7:
        return "churn-heavy"
    if bus_factor == 1 and total_commits > 20:
        return "bus-factor-risk"
    if dep_count >= 5:
        return "high-coupling"
    return "stable"


def _compute_impact_surface(
    target: str,
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
) -> list[dict]:
    """Find the top 3 most critical modules that depend on this file."""
    # BFS up to 2 hops through reverse dependencies
    visited: set[str] = set()
    frontier = {target}
    for _ in range(2):
        next_frontier: set[str] = set()
        for node in frontier:
            for dep in reverse_deps.get(node, set()):
                if dep != target and dep not in visited:
                    visited.add(dep)
                    next_frontier.add(dep)
        frontier = next_frontier

    if not visited:
        return []

    # Rank by pagerank (most critical first)
    ranked = []
    for dep in visited:
        meta = node_meta.get(dep)
        ranked.append({
            "file_path": dep,
            "pagerank": meta.pagerank if meta else 0.0,
            "is_entry_point": meta.is_entry_point if meta else False,
        })
    ranked.sort(key=lambda x: -x["pagerank"])
    return ranked[:3]


@mcp.tool()
async def get_risk(
    targets: list[str],
    repo: str | None = None,
) -> dict:
    """Assess modification risk for one or more files before making changes.

    Pass ALL files you plan to modify in a single call. Returns per-file:
    - hotspot_score and trend ("increasing"/"stable"/"decreasing")
    - risk_type ("churn-heavy"/"bug-prone"/"high-coupling"/"stable")
    - impact_surface: top 3 critical modules that would break
    - dependents, co-change partners, ownership

    Plus the top 5 global hotspots for ambient awareness.

    Example: get_risk(["src/auth/service.py", "src/auth/middleware.py"])

    Args:
        targets: List of file paths to assess.
        repo: Repository path, name, or ID.
    """
    async with get_session(_session_factory) as session:
        repository = await _get_repo(session, repo)
        repo_id = repository.id

        # Pre-load edges
        res = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
            )
        )
        all_edges = res.scalars().all()
        dep_counts: dict[str, int] = {}
        import_links: dict[str, set[str]] = {}
        reverse_deps: dict[str, set[str]] = {}  # target -> set of importers
        for e in all_edges:
            dep_counts[e.target_node_id] = dep_counts.get(e.target_node_id, 0) + 1
            import_links.setdefault(e.source_node_id, set()).add(e.target_node_id)
            import_links.setdefault(e.target_node_id, set()).add(e.source_node_id)
            reverse_deps.setdefault(e.target_node_id, set()).add(e.source_node_id)

        # Pre-load graph nodes for pagerank / impact surface
        node_res = await session.execute(
            select(GraphNode).where(GraphNode.repository_id == repo_id)
        )
        node_meta = {n.node_id: n for n in node_res.scalars().all()}

        # Assess each target
        results = await asyncio.gather(*[
            _assess_one_target(
                session, repository, t, dep_counts, import_links,
                reverse_deps, node_meta,
            )
            for t in targets
        ])

        # Global hotspots (excluding requested targets)
        target_set = set(targets)
        res = await session.execute(
            select(GitMetadata)
            .where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.is_hotspot == True,  # noqa: E712
            )
            .order_by(GitMetadata.churn_percentile.desc())
            .limit(len(targets) + 5)
        )
        all_hotspots = res.scalars().all()
        global_hotspots = [
            {
                "file_path": h.file_path,
                "hotspot_score": h.churn_percentile,
                "primary_owner": h.primary_owner_name,
            }
            for h in all_hotspots
            if h.file_path not in target_set
        ][:5]

    return {
        "targets": {r["target"]: r for r in results},
        "global_hotspots": global_hotspots,
    }


# ---------------------------------------------------------------------------
# Tool 4: get_why (refactored — 3 modes)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_why(
    query: str | None = None,
    targets: list[str] | None = None,
    repo: str | None = None,
) -> dict:
    """Understand why code is built the way it is — intent archaeology.

    Four modes:
    1. get_why("why is auth using JWT?") — semantic + keyword search over decisions
    2. get_why("src/auth/service.py") — all decisions governing a specific file,
       plus origin story and alignment score
    3. get_why("why was caching added?", targets=["src/auth/cache.py"]) —
       target-aware search: prioritizes decisions governing the target files
    4. get_why() — decision health dashboard

    Always call this before making architectural changes.

    Args:
        query: Natural language question, file/module path, or omit for health dashboard.
        targets: Optional file paths to anchor the search. Decisions governing
                 these files are prioritized in results.
        repo: Repository path, name, or ID.
    """
    # --- Mode 1: No query → health dashboard ---
    if not query:
        from repowise.core.persistence.crud import get_decision_health_summary

        async with get_session(_session_factory) as session:
            repository = await _get_repo(session, repo)
            health = await get_decision_health_summary(session, repository.id)

        stale = health["stale_decisions"]
        proposed = health["proposed_awaiting_review"]
        ungoverned = health["ungoverned_hotspots"]

        return {
            "mode": "health",
            "summary": (
                f"{health['summary'].get('active', 0)} active · "
                f"{health['summary'].get('stale', 0)} stale · "
                f"{len(proposed)} proposed · "
                f"{len(ungoverned)} ungoverned hotspots"
            ),
            "counts": health["summary"],
            "stale_decisions": [
                {
                    "id": d.id,
                    "title": d.title,
                    "staleness_score": d.staleness_score,
                    "affected_files": json.loads(d.affected_files_json)[:5],
                }
                for d in stale[:10]
            ],
            "proposed_awaiting_review": [
                {
                    "id": d.id,
                    "title": d.title,
                    "source": d.source,
                    "confidence": d.confidence,
                }
                for d in proposed[:10]
            ],
            "ungoverned_hotspots": ungoverned[:15],
        }

    # --- Mode 2: Path → decisions, origin story, alignment ---
    if _is_path(query):
        async with get_session(_session_factory) as session:
            repository = await _get_repo(session, repo)
            res = await session.execute(
                select(DecisionRecord).where(
                    DecisionRecord.repository_id == repository.id,
                )
            )
            all_decisions = res.scalars().all()

            # Load git metadata for origin story
            git_res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repository.id,
                    GitMetadata.file_path == query,
                )
            )
            git_meta = git_res.scalar_one_or_none()

            # Pre-load all git metadata for cross-file search (used by fallback)
            all_git_res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repository.id,
                )
            )
            all_git_meta = all_git_res.scalars().all()

        governing = []
        for d in all_decisions:
            affected_files = json.loads(d.affected_files_json)
            affected_modules = json.loads(d.affected_modules_json)
            if query in affected_files or query in affected_modules:
                governing.append({
                    "id": d.id,
                    "title": d.title,
                    "status": d.status,
                    "context": d.context,
                    "decision": d.decision,
                    "rationale": d.rationale,
                    "alternatives": json.loads(d.alternatives_json),
                    "consequences": json.loads(d.consequences_json),
                    "affected_files": affected_files,
                    "source": d.source,
                    "confidence": d.confidence,
                    "staleness_score": d.staleness_score,
                })

        result_data: dict[str, Any] = {
            "mode": "path",
            "path": query,
            "decisions": governing,
            "origin_story": _build_origin_story(query, git_meta, governing),
            "alignment": _compute_alignment(query, governing, all_decisions),
        }

        # --- Fallback: git archaeology when no decisions found ---
        if not governing:
            result_data["git_archaeology"] = await _git_archaeology_fallback(
                query, git_meta, all_git_meta, repository,
            )

        return result_data

    # --- Mode 3: Natural language → target-aware search ---
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    async with get_session(_session_factory) as session:
        repository = await _get_repo(session, repo)
        all_decisions = await _list_decisions(
            session, repository.id, include_proposed=True, limit=200
        )

        # Load git metadata for targets (for origin context in results)
        target_git: dict[str, Any] = {}
        if targets:
            for t in targets:
                git_res = await session.execute(
                    select(GitMetadata).where(
                        GitMetadata.repository_id == repository.id,
                        GitMetadata.file_path == t,
                    )
                )
                meta = git_res.scalar_one_or_none()
                if meta:
                    target_git[t] = meta

    # Build target file set for boosting
    target_set = set(targets) if targets else set()

    # Weighted keyword scoring across ALL decision fields
    query_lower = query.lower()
    query_words = set(query_lower.split())
    # Remove stop words for better matching
    stop_words = {"why", "was", "is", "the", "a", "an", "this", "that", "how",
                  "what", "when", "where", "for", "to", "of", "in", "it", "be"}
    query_words -= stop_words

    scored_decisions: list[tuple[float, Any]] = []
    for d in all_decisions:
        score = _score_decision(d, query_words, target_set)
        if score > 0:
            scored_decisions.append((score, d))
    scored_decisions.sort(key=lambda t: t[0], reverse=True)
    keyword_matches = [d for _, d in scored_decisions[:8]]

    # Semantic search over decision vector store
    decision_results = []
    try:
        decision_results = await _decision_store.search(query, limit=5)
    except Exception:
        pass

    # Semantic search over documentation
    doc_results = []
    try:
        doc_results = await _vector_store.search(query, limit=3)
    except Exception:
        try:
            doc_results = await _fts.search(query, limit=3)
        except Exception:
            pass

    # Merge keyword matches with semantic results (dedup by ID)
    seen_ids: set[str] = set()
    merged_decisions = []
    for d in keyword_matches:
        if d.id not in seen_ids:
            seen_ids.add(d.id)
            merged_decisions.append({
                "id": d.id,
                "title": d.title,
                "status": d.status,
                "decision": d.decision,
                "rationale": d.rationale,
                "context": d.context,
                "consequences": json.loads(d.consequences_json),
                "affected_files": json.loads(d.affected_files_json),
                "source": d.source,
                "confidence": d.confidence,
            })

    for r in decision_results:
        if r.page_id not in seen_ids:
            seen_ids.add(r.page_id)
            merged_decisions.append({
                "id": r.page_id,
                "title": r.title,
                "snippet": r.snippet,
                "relevance_score": r.score,
            })

    result_data: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": merged_decisions[:8],
        "related_documentation": [
            {
                "page_id": r.page_id,
                "title": r.title,
                "page_type": r.page_type,
                "snippet": r.snippet,
                "relevance_score": r.score,
            }
            for r in doc_results[:3]
        ],
    }

    # If targets provided, include target context
    if targets:
        async with get_session(_session_factory) as session2:
            # Load all git metadata for cross-file search
            all_git_res = await session2.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repository.id,
                )
            )
            all_git_meta_list = all_git_res.scalars().all()

        target_context = {}
        for t in targets:
            t_governing = []
            for d in all_decisions:
                affected = json.loads(d.affected_files_json)
                affected_mods = json.loads(d.affected_modules_json)
                if t in affected or any(t.startswith(m + "/") for m in affected_mods):
                    t_governing.append({"title": d.title, "status": d.status})
            git_m = target_git.get(t)
            ctx_entry: dict[str, Any] = {
                "governing_decisions": t_governing,
                "origin": _build_origin_story(t, git_m, t_governing) if git_m else {
                    "available": False,
                    "summary": f"No git history for {t}.",
                },
            }
            # Git archaeology fallback when no decisions found
            if not t_governing:
                ctx_entry["git_archaeology"] = await _git_archaeology_fallback(
                    t, git_m, all_git_meta_list, repository,
                )
            target_context[t] = ctx_entry
        result_data["target_context"] = target_context

    return result_data


def _score_decision(
    d: Any, query_words: set[str], target_files: set[str],
) -> float:
    """Score a decision against query words with field weighting and target boosting."""
    if not query_words:
        return 1.0 if target_files else 0.0

    # Build weighted text fields
    fields = [
        (3.0, d.title.lower()),
        (2.0, d.decision.lower()),
        (2.0, d.rationale.lower()),
        (1.5, d.context.lower()),
        (1.0, " ".join(json.loads(d.consequences_json)).lower()),
        (1.0, " ".join(json.loads(d.tags_json)).lower()),
        (1.5, " ".join(json.loads(d.affected_files_json)).lower()),
        (1.0, (d.evidence_file or "").lower()),
    ]

    score = 0.0
    for weight, text in fields:
        for word in query_words:
            if word in text:
                score += weight

    # Target file boosting: decisions governing target files get a bonus
    if target_files:
        affected = set(json.loads(d.affected_files_json))
        affected_mods = json.loads(d.affected_modules_json)
        for t in target_files:
            if t in affected:
                score += 5.0  # Strong boost for exact file match
            elif any(t.startswith(m + "/") for m in affected_mods):
                score += 3.0  # Module-level match

    return score


async def _git_archaeology_fallback(
    file_path: str,
    git_meta: Any | None,
    all_git_meta: list,
    repository: Any,
) -> dict:
    """When no decisions govern a file, mine git history for intent signals."""
    result: dict[str, Any] = {"triggered": True}

    # --- Layer 1: File's own significant commits ---
    file_commits = []
    if git_meta and git_meta.significant_commits_json:
        commits = json.loads(git_meta.significant_commits_json)
        file_commits = [
            {
                "sha": c.get("sha", ""),
                "message": c.get("message", ""),
                "author": c.get("author", ""),
                "date": c.get("date", ""),
            }
            for c in commits
        ]
    result["file_commits"] = file_commits

    # --- Layer 2: Cross-file search — other files' commits mentioning this file ---
    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    # Convert snake_case/kebab to searchable terms: auth_cache_service -> {"auth", "cache", "service"}
    search_terms = set(re.split(r"[_\-/.]", stem.lower()))
    search_terms.discard("")
    # Also search for the full basename
    search_terms.add(basename.lower())

    cross_references = []
    for gm in all_git_meta:
        if gm.file_path == file_path:
            continue
        commits = json.loads(gm.significant_commits_json) if gm.significant_commits_json else []
        for c in commits:
            msg_lower = c.get("message", "").lower()
            # Match if the commit message mentions the file basename or 2+ stem terms
            matched_terms = [t for t in search_terms if t in msg_lower]
            if basename.lower() in msg_lower or len(matched_terms) >= 2:
                cross_references.append({
                    "source_file": gm.file_path,
                    "sha": c.get("sha", ""),
                    "message": c.get("message", ""),
                    "author": c.get("author", ""),
                    "date": c.get("date", ""),
                    "matched_terms": matched_terms,
                })
    # Deduplicate by SHA and sort by date descending
    seen_shas: set[str] = set()
    unique_refs = []
    for cr in cross_references:
        if cr["sha"] not in seen_shas:
            seen_shas.add(cr["sha"])
            unique_refs.append(cr)
    unique_refs.sort(key=lambda x: x.get("date", ""), reverse=True)
    result["cross_references"] = unique_refs[:10]

    # --- Layer 3: Live git log (when local repo exists) ---
    git_log_results = []
    local_path = getattr(repository, "local_path", None)
    if local_path and os.path.isdir(os.path.join(local_path, ".git")):
        git_log_results = await _run_git_log(local_path, file_path, stem)
    result["git_log"] = git_log_results

    # --- Summary ---
    total = len(file_commits) + len(unique_refs) + len(git_log_results)
    if total > 0:
        result["summary"] = (
            f"No architectural decisions found for {file_path}, but git archaeology "
            f"recovered {len(file_commits)} direct commit(s), "
            f"{len(unique_refs)} cross-reference(s), and "
            f"{len(git_log_results)} git log result(s). "
            "Review these to understand the intent behind this code."
        )
    else:
        result["summary"] = (
            f"No architectural decisions or git history found for {file_path}. "
            "This file may be new or not yet indexed."
        )

    return result


async def _run_git_log(
    repo_path: str, file_path: str, stem: str,
) -> list[dict]:
    """Run git log against the local repo for deeper history. Best-effort."""
    import subprocess

    results = []
    try:
        # Search for commits that touched this file
        proc = subprocess.run(
            ["git", "log", "--follow", "--format=%H\t%an\t%ai\t%s", "-20", "--", file_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                parts = line.split("\t", 3)
                if len(parts) == 4:
                    results.append({
                        "sha": parts[0][:12],
                        "author": parts[1],
                        "date": parts[2][:10],
                        "message": parts[3],
                        "source": "git_log_follow",
                    })

        # Also grep for the class/function name in commit messages
        if stem and len(stem) >= 3:
            proc2 = subprocess.run(
                ["git", "log", "--all", "--grep", stem, "--format=%H\t%an\t%ai\t%s", "-10"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc2.returncode == 0:
                seen = {r["sha"] for r in results}
                for line in proc2.stdout.strip().splitlines():
                    parts = line.split("\t", 3)
                    if len(parts) == 4 and parts[0][:12] not in seen:
                        seen.add(parts[0][:12])
                        results.append({
                            "sha": parts[0][:12],
                            "author": parts[1],
                            "date": parts[2][:10],
                            "message": parts[3],
                            "source": "git_log_grep",
                        })
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass  # Git not available or repo not accessible

    return results[:20]


# ---------------------------------------------------------------------------
# Tool 5: search_codebase (unchanged)
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_codebase(
    query: str,
    limit: int = 5,
    page_type: str | None = None,
    repo: str | None = None,
) -> dict:
    """Semantic search over the full wiki. Ask in natural language.

    Args:
        query: Natural language search query (e.g. "how does authentication work?").
        limit: Maximum results to return (default 5).
        page_type: Optional filter by page type (file_page, module_page, etc.).
        repo: Repository path, name, or ID.
    """
    async with get_session(_session_factory) as session:
        # Ensure repo exists
        await _get_repo(session, repo)

    # Try semantic search first, fall back to fulltext
    # Over-fetch when filtering by page_type to avoid returning 0 results
    fetch_limit = limit * 3 if page_type else limit
    results = []
    try:
        results = await _vector_store.search(query, limit=fetch_limit)
    except Exception:
        pass
    if not results:
        try:
            results = await _fts.search(query, limit=fetch_limit)
        except Exception:
            pass

    output = []
    for r in results:
        if page_type and r.page_type != page_type:
            continue
        output.append(
            {
                "page_id": r.page_id,
                "title": r.title,
                "page_type": r.page_type,
                "snippet": r.snippet,
                "relevance_score": r.score,
                "confidence_score": None,
            }
        )

    output = output[:limit]

    # Batch-lookup actual page confidence scores from DB
    if output:
        page_ids = [item["page_id"] for item in output]
        async with get_session(_session_factory) as session:
            res = await session.execute(
                select(Page.id, Page.confidence).where(Page.id.in_(page_ids))
            )
            conf_map = {row[0]: row[1] for row in res.all()}
        for item in output:
            item["confidence_score"] = conf_map.get(item["page_id"])

    return {"results": output}


# ---------------------------------------------------------------------------
# Tool 6: get_dependency_path (unchanged)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_dependency_path(
    source: str, target: str, repo: str | None = None
) -> dict:
    """Find how two files/modules are connected in the dependency graph.

    When no direct path exists, returns visual context: nearest common
    ancestors, shared neighbors, community analysis, and bridge suggestions
    to help debug architectural silos.

    Args:
        source: Source file or module path.
        target: Target file or module path.
        repo: Repository path, name, or ID.
    """
    async with get_session(_session_factory) as session:
        repository = await _get_repo(session, repo)

        edge_result = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repository.id,
            )
        )
        edges = edge_result.scalars().all()

        node_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
            )
        )
        nodes = node_result.scalars().all()

    try:
        import networkx as nx
    except ImportError:
        return {"path": [], "distance": -1, "explanation": "networkx not available for path queries"}

    G = nx.DiGraph()
    for e in edges:
        G.add_edge(
            e.source_node_id,
            e.target_node_id,
            edge_type=getattr(e, "edge_type", None) or "imports",
        )

    if source not in G:
        return {"path": [], "distance": -1, "explanation": f"Source node '{source}' not found in graph"}
    if target not in G:
        return {"path": [], "distance": -1, "explanation": f"Target node '{target}' not found in graph"}

    try:
        path = nx.shortest_path(G, source, target)
    except nx.NetworkXNoPath:
        return {
            "path": [],
            "distance": -1,
            "explanation": "No direct dependency path found",
            "visual_context": _build_visual_context(G, source, target, nodes, nx),
        }

    # Build path with relationships
    path_with_info = []
    for i, node in enumerate(path):
        relationship = ""
        if i < len(path) - 1:
            next_node = path[i + 1]
            relationship = G[node][next_node].get("edge_type", "imports")
        path_with_info.append({"node": node, "relationship": relationship})

    return {
        "path": path_with_info,
        "distance": len(path) - 1,
        "explanation": f"Shortest path from {source} to {target} has {len(path) - 1} hops",
    }


def _build_visual_context(
    G: Any, source: str, target: str, nodes: list, nx: Any,
) -> dict:
    """Build diagnostic context when no directed path exists."""
    node_meta = {n.node_id: n for n in nodes}
    context: dict[str, Any] = {}

    # --- Reverse path check ---
    try:
        rev_path = nx.shortest_path(G, target, source)
        context["reverse_path"] = {
            "exists": True,
            "path": rev_path,
            "distance": len(rev_path) - 1,
            "note": f"A path exists in the reverse direction ({target} -> {source}). "
                    "The dependency flows the other way.",
        }
    except nx.NetworkXNoPath:
        context["reverse_path"] = {"exists": False}

    # --- Nearest common ancestors (via undirected graph) ---
    U = G.to_undirected()
    source_reachable = set(nx.single_source_shortest_path_length(U, source))
    target_reachable = set(nx.single_source_shortest_path_length(U, target))
    common = source_reachable & target_reachable
    common.discard(source)
    common.discard(target)

    if common:
        source_dist = nx.single_source_shortest_path_length(U, source)
        target_dist = nx.single_source_shortest_path_length(U, target)
        scored = [
            (node, source_dist[node] + target_dist[node])
            for node in common
        ]
        scored.sort(key=lambda x: x[1])
        context["nearest_common_ancestors"] = [
            {"node": node, "distance_from_source": source_dist[node],
             "distance_from_target": target_dist[node]}
            for node, _ in scored[:5]
        ]
    else:
        context["nearest_common_ancestors"] = []

    # --- Shared neighbors (direct) ---
    source_neighbors = set(G.predecessors(source)) | set(G.successors(source))
    target_neighbors = set(G.predecessors(target)) | set(G.successors(target))
    shared = source_neighbors & target_neighbors
    if shared:
        context["shared_neighbors"] = sorted(shared)
    else:
        context["shared_neighbors"] = []

    # --- Community analysis ---
    src_meta = node_meta.get(source)
    tgt_meta = node_meta.get(target)
    src_community = src_meta.community_id if src_meta else None
    tgt_community = tgt_meta.community_id if tgt_meta else None

    context["community"] = {
        "source_community": src_community,
        "target_community": tgt_community,
        "same_community": src_community is not None and src_community == tgt_community,
    }

    # --- Bridge suggestions: high-centrality nodes between communities ---
    if src_community is not None and tgt_community is not None and src_community != tgt_community:
        # Find nodes that have edges crossing these two communities
        bridge_nodes = []
        nodes_by_community: dict[int, set[str]] = {}
        for n in nodes:
            nodes_by_community.setdefault(n.community_id, set()).add(n.node_id)

        src_community_nodes = nodes_by_community.get(src_community, set())
        tgt_community_nodes = nodes_by_community.get(tgt_community, set())

        for node_id in G.nodes():
            neighbors = set(G.predecessors(node_id)) | set(G.successors(node_id))
            touches_src = bool(neighbors & src_community_nodes)
            touches_tgt = bool(neighbors & tgt_community_nodes)
            if touches_src and touches_tgt:
                meta = node_meta.get(node_id)
                bridge_nodes.append({
                    "node": node_id,
                    "pagerank": meta.pagerank if meta else 0.0,
                })
        bridge_nodes.sort(key=lambda x: x["pagerank"], reverse=True)
        context["bridge_suggestions"] = bridge_nodes[:5]
    else:
        context["bridge_suggestions"] = []

    # --- Connectivity summary ---
    # Check if they're in completely disconnected components
    components = list(nx.weakly_connected_components(G))
    src_comp = next((c for c in components if source in c), set())
    tgt_comp = next((c for c in components if target in c), set())
    actually_disconnected = src_comp != tgt_comp

    if actually_disconnected:
        context["disconnected"] = True
        context["source_component_size"] = len(src_comp)
        context["target_component_size"] = len(tgt_comp)
        context["suggestion"] = (
            "These nodes are in completely separate dependency clusters with "
            "no shared connections. Look for shared configuration files, API "
            "contracts, or event buses that should bridge them."
        )
    else:
        context["disconnected"] = False
        if context["nearest_common_ancestors"]:
            top = context["nearest_common_ancestors"][0]["node"]
            context["suggestion"] = (
                f"No direct path, but both nodes connect through '{top}'. "
                "This shared dependency may be the architectural bridge point."
            )
        elif context["shared_neighbors"]:
            context["suggestion"] = (
                f"No direct path, but they share neighbor(s): "
                f"{', '.join(context['shared_neighbors'])}. "
                "These shared files may serve as the missing link."
            )
        elif context["reverse_path"].get("exists"):
            context["suggestion"] = (
                "No direct path in this direction, but a reverse path exists. "
                "The dependency flows the other way."
            )
        else:
            context["suggestion"] = (
                "These nodes are in the same cluster but have no direct "
                "or reverse dependency. Check for indirect connections."
            )

    return context


# ---------------------------------------------------------------------------
# Tool 7: get_dead_code (tiered refactor plan)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_dead_code(
    repo: str | None = None,
    kind: str | None = None,
    min_confidence: float = 0.5,
    safe_only: bool = False,
    limit: int = 20,
    tier: str | None = None,
    directory: str | None = None,
    owner: str | None = None,
    group_by: str | None = None,
) -> dict:
    """Get a tiered refactor plan for dead and unused code.

    Returns findings organized into confidence tiers (high/medium/low),
    with per-directory rollups, ownership hotspots, and impact estimates
    so you can prioritize cleanup.

    Use group_by="directory" for a directory-level overview, or
    group_by="owner" to see who owns the most dead code. Use tier
    to focus on a single confidence band.

    Args:
        repo: Repository path, name, or ID.
        kind: Filter by kind (unreachable_file, unused_export, unused_internal, zombie_package).
        min_confidence: Minimum confidence threshold (default 0.5).
        safe_only: Only return findings marked safe_to_delete (default false).
        limit: Maximum findings per tier (default 20).
        tier: Focus on a single tier: "high" (>=0.8), "medium" (0.5-0.8), or "low" (<0.5).
        directory: Filter findings to a specific directory prefix.
        owner: Filter findings by primary owner name.
        group_by: "directory" for per-directory rollup, "owner" for ownership hotspots.
    """
    async with get_session(_session_factory) as session:
        repository = await _get_repo(session, repo)

        # Fetch all open findings for summary computation
        all_query = select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository.id,
            DeadCodeFinding.status == "open",
        )
        all_result = await session.execute(all_query)
        all_findings = list(all_result.scalars().all())

    # --- Apply filters ---
    filtered = all_findings
    if kind:
        filtered = [f for f in filtered if f.kind == kind]
    if safe_only:
        filtered = [f for f in filtered if f.safe_to_delete]
    if min_confidence > 0:
        filtered = [f for f in filtered if f.confidence >= min_confidence]
    if directory:
        prefix = directory.rstrip("/") + "/"
        filtered = [f for f in filtered if f.file_path.startswith(prefix)]
    if owner:
        owner_lower = owner.lower()
        filtered = [f for f in filtered if f.primary_owner and f.primary_owner.lower() == owner_lower]

    # --- Build tiered structure ---
    tiers = _build_tiers(filtered, limit, tier)

    # --- Summary across ALL open findings (unfiltered) ---
    by_kind: dict[str, int] = {}
    for f in all_findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    summary = {
        "total_findings": len(all_findings),
        "filtered_findings": len(filtered),
        "deletable_lines": sum(f.lines for f in all_findings if f.safe_to_delete),
        "safe_to_delete_count": sum(1 for f in all_findings if f.safe_to_delete),
        "by_kind": by_kind,
    }

    result: dict[str, Any] = {"summary": summary, "tiers": tiers}

    # --- Grouping views ---
    if group_by == "directory":
        result["by_directory"] = _rollup_by_directory(filtered)
    elif group_by == "owner":
        result["by_owner"] = _rollup_by_owner(filtered)

    # --- Impact estimate ---
    result["impact"] = _compute_impact(tiers)

    return result


def _serialize_finding(f: Any) -> dict:
    """Serialize a single DeadCodeFinding to dict."""
    return {
        "kind": f.kind,
        "file_path": f.file_path,
        "symbol_name": f.symbol_name,
        "confidence": f.confidence,
        "reason": f.reason,
        "safe_to_delete": f.safe_to_delete,
        "lines": f.lines,
        "last_commit_at": f.last_commit_at.isoformat() if f.last_commit_at else None,
        "primary_owner": f.primary_owner,
        "age_days": f.age_days,
    }


def _build_tiers(
    findings: list, limit: int, tier_filter: str | None,
) -> dict:
    """Split findings into high/medium/low confidence tiers."""
    high = sorted(
        [f for f in findings if f.confidence >= 0.8],
        key=lambda f: (-f.confidence, -f.lines),
    )
    medium = sorted(
        [f for f in findings if 0.5 <= f.confidence < 0.8],
        key=lambda f: (-f.confidence, -f.lines),
    )
    low = sorted(
        [f for f in findings if f.confidence < 0.5],
        key=lambda f: (-f.confidence, -f.lines),
    )

    def _tier_block(name: str, items: list, description: str) -> dict:
        return {
            "description": description,
            "count": len(items),
            "lines": sum(f.lines for f in items),
            "safe_count": sum(1 for f in items if f.safe_to_delete),
            "findings": [_serialize_finding(f) for f in items[:limit]],
            "truncated": len(items) > limit,
        }

    tiers = {}
    if tier_filter is None or tier_filter == "high":
        tiers["high"] = _tier_block(
            "high", high,
            "High confidence (>=0.8): Zero references in the codebase. Safe to delete.",
        )
    if tier_filter is None or tier_filter == "medium":
        tiers["medium"] = _tier_block(
            "medium", medium,
            "Medium confidence (0.5-0.8): Likely unused but may have indirect references. Review before deleting.",
        )
    if tier_filter is None or tier_filter == "low":
        tiers["low"] = _tier_block(
            "low", low,
            "Low confidence (<0.5): Potentially used via dynamic imports or reflection. Investigate first.",
        )
    return tiers


def _rollup_by_directory(findings: list) -> list[dict]:
    """Group findings by top-level directory."""
    dirs: dict[str, dict] = {}
    for f in findings:
        parts = f.file_path.split("/")
        # Use first two path segments as directory key, or just the first
        dir_key = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
        if dir_key not in dirs:
            dirs[dir_key] = {"directory": dir_key, "count": 0, "lines": 0, "safe_count": 0}
        dirs[dir_key]["count"] += 1
        dirs[dir_key]["lines"] += f.lines
        if f.safe_to_delete:
            dirs[dir_key]["safe_count"] += 1

    return sorted(dirs.values(), key=lambda d: -d["lines"])


def _rollup_by_owner(findings: list) -> list[dict]:
    """Group findings by primary owner."""
    owners: dict[str, dict] = {}
    for f in findings:
        name = f.primary_owner or "unowned"
        if name not in owners:
            owners[name] = {"owner": name, "count": 0, "lines": 0, "safe_count": 0}
        owners[name]["count"] += 1
        owners[name]["lines"] += f.lines
        if f.safe_to_delete:
            owners[name]["safe_count"] += 1

    return sorted(owners.values(), key=lambda o: -o["lines"])


def _compute_impact(tiers: dict) -> dict:
    """Compute total impact across tiers."""
    total_lines = 0
    safe_lines = 0
    for tier_data in tiers.values():
        total_lines += tier_data["lines"]
        # Approximate safe lines from findings in the tier
        for f in tier_data["findings"]:
            if f["safe_to_delete"]:
                safe_lines += f["lines"]

    return {
        "total_lines_reclaimable": total_lines,
        "safe_lines_reclaimable": safe_lines,
        "recommendation": (
            "Start with the 'high' tier — these have zero references and are safe to remove. "
            "Then review 'medium' tier findings with your team."
            if total_lines > 0
            else "No dead code found matching your filters."
        ),
    }


# ---------------------------------------------------------------------------
# Tool 8: get_architecture_diagram (unchanged)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_architecture_diagram(
    scope: str = "repo",
    path: str | None = None,
    diagram_type: str = "auto",
    repo: str | None = None,
) -> dict:
    """Get a Mermaid diagram for the codebase or a specific module.

    Args:
        scope: "repo", "module", or "file".
        path: Module or file path (required for module/file scope).
        diagram_type: "auto", "flowchart", "class", or "sequence".
        repo: Repository path, name, or ID.
    """
    async with get_session(_session_factory) as session:
        repository = await _get_repo(session, repo)

        if scope == "repo":
            # Return the architecture diagram page
            result = await session.execute(
                select(Page).where(
                    Page.repository_id == repository.id,
                    Page.page_type == "architecture_diagram",
                )
            )
            page = result.scalar_one_or_none()
            if page:
                return {
                    "diagram_type": diagram_type if diagram_type != "auto" else "flowchart",
                    "mermaid_syntax": page.content,
                    "description": page.title,
                }

        # For module/file scope or fallback, build diagram from graph
        if path:
            filter_prefix = path
        else:
            filter_prefix = ""

        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.node_id.like(f"{filter_prefix}%") if filter_prefix else GraphNode.repository_id == repository.id,
            )
        )
        nodes = result.scalars().all()

        result = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repository.id,
            )
        )
        edges = result.scalars().all()

        node_ids = {n.node_id for n in nodes}
        pr_map = {n.node_id: n.pagerank for n in nodes}
        relevant_edges = sorted(
            [
                e for e in edges
                if e.source_node_id in node_ids or e.target_node_id in node_ids
            ],
            key=lambda e: pr_map.get(e.source_node_id, 0.0),
            reverse=True,
        )

        # Build Mermaid flowchart
        lines = ["graph TD"]
        seen_nodes = set()
        for e in relevant_edges[:50]:  # Limit to 50 edges for readability
            src = _sanitize_mermaid_id(e.source_node_id)
            tgt = _sanitize_mermaid_id(e.target_node_id)
            if src not in seen_nodes:
                lines.append(f'    {src}["{e.source_node_id}"]')
                seen_nodes.add(src)
            if tgt not in seen_nodes:
                lines.append(f'    {tgt}["{e.target_node_id}"]')
                seen_nodes.add(tgt)
            lines.append(f"    {src} --> {tgt}")

        mermaid = "\n".join(lines) if len(lines) > 1 else "graph TD\n    A[No graph data available]"

        return {
            "diagram_type": diagram_type if diagram_type != "auto" else "flowchart",
            "mermaid_syntax": mermaid,
            "description": f"Dependency graph for {scope}: {path or 'entire repo'}",
        }


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------


def create_mcp_server(repo_path: str | None = None) -> FastMCP:
    """Create and return the MCP server instance, optionally scoped to a repo."""
    global _repo_path  # noqa: PLW0603
    _repo_path = repo_path
    return mcp


def run_mcp(
    transport: str = "stdio",
    repo_path: str | None = None,
    port: int = 7338,
) -> None:
    """Run the MCP server with the specified transport."""
    global _repo_path  # noqa: PLW0603
    _repo_path = repo_path

    if transport == "sse":
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")

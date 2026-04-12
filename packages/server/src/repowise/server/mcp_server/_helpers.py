"""Shared helpers used by multiple MCP tool modules."""

from __future__ import annotations

import json
import os
import os.path
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    Repository,
)
from repowise.server.mcp_server import _state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CODE_EXTS = frozenset(
    {
        ".py",
        ".ts",
        ".js",
        ".go",
        ".rs",
        ".java",
        ".tsx",
        ".jsx",
        ".rb",
        ".kt",
        ".cpp",
        ".c",
        ".h",
        ".cs",
        ".swift",
        ".scala",
    }
)


# ---------------------------------------------------------------------------
# Repository resolution
# ---------------------------------------------------------------------------


async def _get_repo(session: AsyncSession, repo: str | None = None) -> Repository:
    """Resolve a repository — by path, by ID, or return the first one."""
    if repo:
        # Try by path
        result = await session.execute(select(Repository).where(Repository.local_path == repo))
        obj = result.scalar_one_or_none()
        if obj:
            return obj
        # Try by ID
        obj = await session.get(Repository, repo)
        if obj:
            return obj
        # Try by name
        result = await session.execute(select(Repository).where(Repository.name == repo))
        obj = result.scalar_one_or_none()
        if obj:
            return obj
        raise LookupError(f"Repository not found: {repo}")

    # Default: return the first (and often only) repository
    result = await session.execute(select(Repository).limit(1))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise LookupError("No repositories found. Run 'repowise init' first.")
    return obj


# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------


def _is_path(query: str) -> bool:
    """Heuristic: does this string look like a file or module path?"""
    if "/" in query:
        return True
    _, ext = os.path.splitext(query)
    return ext in _CODE_EXTS


# ---------------------------------------------------------------------------
# Workspace-aware repo context resolution
# ---------------------------------------------------------------------------


def _is_workspace_mode() -> bool:
    """Return True if the MCP server is running in workspace mode."""
    return _state._registry is not None


async def _resolve_repo_context(repo: str | None = None) -> Any:
    """Resolve the per-repo resource context for the given ``repo`` parameter.

    In **single-repo mode** (no registry): returns a lightweight wrapper
    around the existing ``_state`` globals — zero overhead, full backward
    compatibility.

    In **workspace mode**: resolves the alias via the registry and returns
    the matching ``RepoContext``.

    Raises ``ValueError`` for ``repo="all"`` — callers must handle that
    case explicitly before calling this helper.
    """
    from repowise.core.workspace.registry import RepoContext

    registry = _state._registry
    if registry is None:
        # Single-repo mode — validate the repo param against the DB if given
        if repo is not None:
            from repowise.core.persistence.database import get_session as _get_session

            async with _get_session(_state._session_factory) as session:
                await _get_repo(session, repo)  # raises LookupError if invalid

        return RepoContext(
            alias="default",
            path=Path(_state._repo_path) if _state._repo_path else Path.cwd(),
            session_factory=_state._session_factory,
            fts=_state._fts,
            vector_store=_state._vector_store,
            decision_store=_state._decision_store,
            vector_store_ready=_state._vector_store_ready or __import__("asyncio").Event(),
            _engine=None,
        )

    # Workspace mode — resolve via registry
    resolved = registry.resolve_repo_param(repo)
    if isinstance(resolved, list):
        raise ValueError(
            "repo='all' must be handled explicitly by each tool. "
            "Use _resolve_all_contexts() instead."
        )
    return await registry.get(resolved)


async def _resolve_all_contexts() -> list[Any]:
    """Return ``RepoContext`` objects for all repos in the workspace.

    In single-repo mode, returns a single-element list wrapping ``_state``.
    """
    registry = _state._registry
    if registry is None:
        ctx = await _resolve_repo_context(None)
        return [ctx]
    contexts = []
    for alias in registry.get_all_aliases():
        contexts.append(await registry.get(alias))
    return contexts


def _unsupported_repo_all(tool_name: str) -> dict:
    """Return an error dict for tools that don't support ``repo='all'``."""
    registry = _state._registry
    if registry is not None:
        available = registry.get_all_aliases()
    else:
        available = []
    return {
        "error": (
            f"repo='all' is not supported for {tool_name}. "
            f"Specify a repo alias instead. Available: {available}"
        ),
    }


# ---------------------------------------------------------------------------
# Origin story & alignment (used by get_context, get_why)
# ---------------------------------------------------------------------------


def _build_origin_story(
    file_path: str,
    git_meta: Any | None,
    governing_decisions: list[dict],
) -> dict:
    """Build the human context / origin story for a file from stored metadata."""
    if git_meta is None:
        return {
            "available": False,
            "summary": f"No git history available for {file_path}.",
        }

    authors = json.loads(git_meta.top_authors_json) if git_meta.top_authors_json else []
    commits = (
        json.loads(git_meta.significant_commits_json) if git_meta.significant_commits_json else []
    )

    # Find the earliest significant commit as the "creation" context
    earliest_commit = None
    if commits:
        sorted_commits = sorted(commits, key=lambda c: c.get("date", ""))
        earliest_commit = sorted_commits[0]

    # Link commits to decisions via keyword overlap
    linked_decisions = []
    for d in governing_decisions:
        # Build a keyword set from the decision
        decision_text = (
            f"{d.get('title', '')} {d.get('decision', '')} {d.get('rationale', '')}".lower()
        )
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
                related_commits.append(
                    {
                        "sha": c.get("sha", ""),
                        "message": c.get("message", ""),
                        "author": c.get("author", ""),
                        "date": c.get("date", ""),
                        "matching_keywords": sorted(overlap)[:5],
                    }
                )

        linked_decisions.append(
            {
                "title": d.get("title", ""),
                "status": d.get("status", ""),
                "source": d.get("source", ""),
                "rationale": d.get("rationale", ""),
                "evidence_commits": related_commits,
            }
        )

    # Build narrative summary
    primary = git_meta.primary_owner_name or "unknown"
    total = git_meta.commit_count_total or 0
    first_date = (
        git_meta.first_commit_at.strftime("%Y-%m-%d") if git_meta.first_commit_at else "unknown"
    )
    last_date = (
        git_meta.last_commit_at.strftime("%Y-%m-%d") if git_meta.last_commit_at else "unknown"
    )
    age = git_meta.age_days or 0

    parts = [f"Created ~{first_date}, last modified {last_date} ({age} days old)."]
    parts.append(f"Primary author: {primary} ({total} total commits).")

    if earliest_commit:
        parts.append(
            f'Earliest key commit: "{earliest_commit.get("message", "")}" '
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
                    f'Commit "{ec["message"]}" by {ec["author"]} is evidence for "{ld["title"]}".'
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
    file_path: str,
    governing: list[dict],
    all_decisions: list,
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
        _affected_modules = json.loads(d.affected_modules_json)
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
            "All governing decisions are deprecated/superseded. "
            "This file likely contains technical debt that should be migrated."
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

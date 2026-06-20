"""Shared helpers used by multiple MCP tool modules."""

from __future__ import annotations

import json
import os
import os.path
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.persistence.models import (
    Repository,
)
from repowise.server.mcp_server import _state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CODE_EXTS = _LANG_REGISTRY.all_code_extensions()

# Words that mark a string as a natural-language question rather than a path.
# Keep this small — false positives here send genuine paths to the NL branch,
# which is harmless (path lookup also runs as a fallback) but slower.
_NL_QUESTION_TOKENS = frozenset(
    {
        "why",
        "how",
        "what",
        "when",
        "where",
        "who",
        "which",
        "should",
        "can",
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
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
    """Heuristic: does this string look like a file or module path?

    Natural-language questions take precedence over the slash heuristic
    because phrases like "two-phase plan/apply flow" or "client/server
    boundary" contain a slash without being paths. We treat anything with
    a question mark, that starts with a question word, or that has 4+
    whitespace-separated tokens including a question word, as NL.
    """
    stripped = query.strip()
    if not stripped:
        return False

    # Trailing "?" is an unambiguous NL signal.
    if stripped.endswith("?"):
        return False

    tokens = stripped.split()

    # First token is a question word → NL.
    if tokens and tokens[0].lower().rstrip(",.;:") in _NL_QUESTION_TOKENS:
        return False

    # Sentence-shaped input (multiple words including a question word) → NL.
    if len(tokens) >= 4 and any(t.lower().rstrip(",.;:") in _NL_QUESTION_TOKENS for t in tokens):
        return False

    # A path can't contain whitespace.
    if any(ch.isspace() for ch in stripped):
        return False

    if "/" in stripped or "\\" in stripped:
        return True
    _, ext = os.path.splitext(stripped)
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

# Common stop-words stripped before commit/decision keyword overlap.
_ORIGIN_STOP_WORDS = frozenset(
    {"the", "a", "an", "is", "for", "to", "of", "in", "and", "or", "with"}
)


def _meaningful_words(text: str) -> set[str]:
    """Lowercase keyword set with common stop-words removed."""
    return set(text.lower().split()) - _ORIGIN_STOP_WORDS


def _commits_matching_decision(decision: dict, commits: list[dict]) -> list[dict]:
    """Return commits whose messages share at least one keyword with *decision*."""
    decision_text = (
        f"{decision.get('title', '')} {decision.get('decision', '')} "
        f"{decision.get('rationale', '')}"
    )
    decision_words = _meaningful_words(decision_text)

    related_commits = []
    for c in commits:
        overlap = decision_words & _meaningful_words(c.get("message", ""))
        if not overlap:
            continue
        related_commits.append(
            {
                "sha": c.get("sha", ""),
                "message": c.get("message", ""),
                "author": c.get("author", ""),
                "date": c.get("date", ""),
                "matching_keywords": sorted(overlap)[:5],
            }
        )
    return related_commits


def _link_decisions_to_commits(governing_decisions: list[dict], commits: list[dict]) -> list[dict]:
    """Attach commit evidence to each governing decision via keyword overlap."""
    linked_decisions = []
    for d in governing_decisions:
        linked_decisions.append(
            {
                "title": d.get("title", ""),
                "status": d.get("status", ""),
                "source": d.get("source", ""),
                "rationale": d.get("rationale", ""),
                "evidence_commits": _commits_matching_decision(d, commits),
            }
        )
    return linked_decisions


def _origin_summary_parts(
    authors: list,
    earliest_commit: dict | None,
    linked_decisions: list[dict],
    primary: str,
    total: int,
    first_date: str,
    last_date: str,
    age: int,
) -> list[str]:
    """Assemble the narrative sentences for an origin story."""
    parts = [
        f"Created ~{first_date}, last modified {last_date} ({age} days old).",
        f"Primary author: {primary} ({total} total commits).",
    ]

    if earliest_commit:
        parts.append(
            f'Earliest key commit: "{earliest_commit.get("message", "")}" '
            f"by {earliest_commit.get('author', 'unknown')} on {earliest_commit.get('date', 'unknown')}."
        )

    if linked_decisions:
        decision_titles = [d["title"] for d in linked_decisions[:3]]
        parts.append(f"Governed by: {', '.join(decision_titles)}.")
        for ld in linked_decisions:
            if ld["evidence_commits"]:
                ec = ld["evidence_commits"][0]
                parts.append(
                    f'Commit "{ec["message"]}" by {ec["author"]} is evidence for "{ld["title"]}".'
                )

    if len(authors) > 1:
        names = [a.get("name", "") for a in authors[:3]]
        parts.append(f"Contributors: {', '.join(names)}.")

    return parts


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
        earliest_commit = sorted(commits, key=lambda c: c.get("date", ""))[0]

    linked_decisions = _link_decisions_to_commits(governing_decisions, commits)

    primary = git_meta.primary_owner_name or "unknown"
    total = git_meta.commit_count_total or 0
    first_date = (
        git_meta.first_commit_at.strftime("%Y-%m-%d") if git_meta.first_commit_at else "unknown"
    )
    last_date = (
        git_meta.last_commit_at.strftime("%Y-%m-%d") if git_meta.last_commit_at else "unknown"
    )
    age = git_meta.age_days or 0

    parts = _origin_summary_parts(
        authors,
        earliest_commit,
        linked_decisions,
        primary,
        total,
        first_date,
        last_date,
        age,
    )

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


def _sibling_coverage(file_path: str, governing: list[dict], all_decisions: list) -> float | None:
    """Fraction of sibling-file decisions that also cover *file_path* (None if no siblings)."""
    dir_path = "/".join(file_path.split("/")[:-1])
    sibling_decision_ids = set()
    file_decision_titles = {d["title"] for d in governing}

    for d in all_decisions:
        affected = json.loads(d.affected_files_json)
        json.loads(d.affected_modules_json)
        for af in affected:
            af_dir = "/".join(af.split("/")[:-1])
            if af_dir == dir_path and af != file_path:
                sibling_decision_ids.add(d.title)

    if not sibling_decision_ids:
        return None  # No siblings to compare
    shared = file_decision_titles & sibling_decision_ids
    return len(shared) / len(sibling_decision_ids)


def _active_alignment(active: list, dir_path: str, sibling_coverage: float | None) -> tuple:
    """Score/explanation when active decisions govern the file."""
    if sibling_coverage is not None and sibling_coverage >= 0.5:
        return "high", (
            f"Follows {len(active)} active decision(s) shared with sibling files. "
            f"This file aligns with established patterns in {dir_path}/."
        )
    if sibling_coverage is not None and sibling_coverage < 0.5:
        return "medium", (
            f"Has {len(active)} active decision(s) but limited overlap with "
            f"sibling files in {dir_path}/. May use a different pattern than neighbors."
        )
    return "high", f"Governed by {len(active)} active decision(s)."


def _alignment_score(
    governing: list[dict],
    active: list,
    deprecated: list,
    stale: list,
    proposed: list,
    dir_path: str,
    sibling_coverage: float | None,
) -> tuple:
    """Derive the (score, explanation) tuple from decision status counts."""
    if deprecated and not active and not proposed:
        return "low", (
            "All governing decisions are deprecated/superseded. "
            "This file likely contains technical debt that should be migrated."
        )
    if stale and len(stale) >= len(governing) / 2:
        return "low", (
            f"{len(stale)} of {len(governing)} governing decision(s) are stale. "
            f"The architectural rationale may no longer apply."
        )
    if active:
        return _active_alignment(active, dir_path, sibling_coverage)
    if proposed:
        return "medium", (
            f"Governed by {len(proposed)} proposed (unreviewed) decision(s). "
            f"Patterns are established but not yet formally approved."
        )
    return "medium", f"Governed by {len(governing)} decision(s) with mixed status."


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

    dir_path = "/".join(file_path.split("/")[:-1])
    sibling_coverage = _sibling_coverage(file_path, governing, all_decisions)

    score, explanation = _alignment_score(
        governing, active, deprecated, stale, proposed, dir_path, sibling_coverage
    )

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
# Per-repo exclude_patterns filtering (issue 5 of #296)
#
# Excluded files are skipped at ingest time, but rows may predate an
# exclude_patterns change, so MCP tools filter their results at query time too.
# ---------------------------------------------------------------------------


def _get_exclude_spec(repo_path: "Path | str") -> "Any":
    """Compile the repo's exclusion rules into a PathSpec, or None.

    Unions ``.repowise/config.yaml`` ``exclude_patterns`` with the repo's
    gitignore stack (``.gitignore`` + ``.git/info/exclude``). Indexes built
    before the traverser honoured ``info/exclude`` still contain rows for
    local-only scratch dirs; filtering them at query time keeps those paths
    out of tool responses without forcing a reindex.
    """
    from pathlib import Path

    import pathspec

    from repowise.core.repo_config import load_repo_config

    patterns = list(load_repo_config(repo_path).get("exclude_patterns") or [])
    root = Path(repo_path)
    for ignore_file in (root / ".gitignore", root / ".git" / "info" / "exclude"):
        try:
            if ignore_file.exists():
                patterns.extend(
                    ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                )
        except OSError:
            continue
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def is_excluded(path: "str | None", spec: "Any") -> bool:
    """True if *path* matches *spec* (None spec or path -> not excluded)."""
    return bool(spec is not None and path and spec.match_file(path))


def filter_rows_by_attr(rows: list, attr: str, spec: "Any") -> list:
    """Shape A: drop ORM rows whose ``attr`` path is excluded."""
    if not spec:
        return rows
    return [r for r in rows if not is_excluded(getattr(r, attr, None), spec)]


def filter_graph_nodes(nodes: list, spec: "Any") -> list:
    """Shape B: file nodes match on ``node_id``, symbol nodes on ``file_path``."""
    if not spec:
        return nodes
    out = []
    for n in nodes:
        path = n.node_id if getattr(n, "node_type", None) == "file" else n.file_path
        if is_excluded(path, spec):
            continue
        out.append(n)
    return out


def filter_dicts_by_key(items: list, key: str, spec: "Any") -> list:
    """Shape C: drop result dicts whose ``key`` path is excluded."""
    if not spec:
        return items
    return [d for d in items if not is_excluded(d.get(key), spec)]


def filter_path_list(paths: "list | None", spec: "Any") -> list:
    """Shape D: filter a list of path strings (None -> [])."""
    if not paths:
        return []
    if not spec:
        return list(paths)
    return [p for p in paths if not is_excluded(p, spec)]


def filter_embedded_path_ids(ids: list, spec: "Any") -> list:
    """Shape E: ids look like ``"path::Name"``; match on the file portion."""
    if not spec:
        return ids
    return [i for i in ids if not is_excluded(i.split("::", 1)[0], spec)]

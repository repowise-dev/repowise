"""MCP Tool 4: get_why — intent archaeology and decision search."""

from __future__ import annotations

import contextlib
import json
import os
import re
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import (
    _build_origin_story,
    _compute_alignment,
    _get_repo,
    _is_path,
)
from repowise.server.mcp_server._server import mcp


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

        async with get_session(_state._session_factory) as session:
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
        async with get_session(_state._session_factory) as session:
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
                    governing.append(
                        {
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
                        }
                    )

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
                    query,
                    git_meta,
                    all_git_meta,
                    repository,
                )

            return result_data

    # --- Mode 3: Natural language → target-aware search ---
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    async with get_session(_state._session_factory) as session:
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
    stop_words = {
        "why",
        "was",
        "is",
        "the",
        "a",
        "an",
        "this",
        "that",
        "how",
        "what",
        "when",
        "where",
        "for",
        "to",
        "of",
        "in",
        "it",
        "be",
    }
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
    with contextlib.suppress(Exception):
        decision_results = await _state._decision_store.search(query, limit=5)

    # Semantic search over documentation
    doc_results = []
    try:
        doc_results = await _state._vector_store.search(query, limit=3)
    except Exception:
        with contextlib.suppress(Exception):
            doc_results = await _state._fts.search(query, limit=3)

    # Merge keyword matches with semantic results (dedup by ID)
    seen_ids: set[str] = set()
    merged_decisions = []
    for d in keyword_matches:
        if d.id not in seen_ids:
            seen_ids.add(d.id)
            merged_decisions.append(
                {
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
                }
            )

    for r in decision_results:
        if r.page_id not in seen_ids:
            seen_ids.add(r.page_id)
            merged_decisions.append(
                {
                    "id": r.page_id,
                    "title": r.title,
                    "snippet": r.snippet,
                    "relevance_score": r.score,
                }
            )

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
        async with get_session(_state._session_factory) as session2:
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
                    "origin": _build_origin_story(t, git_m, t_governing)
                    if git_m
                    else {
                        "available": False,
                        "summary": f"No git history for {t}.",
                    },
                }
                # Git archaeology fallback when no decisions found
                if not t_governing:
                    ctx_entry["git_archaeology"] = await _git_archaeology_fallback(
                        t,
                        git_m,
                        all_git_meta_list,
                        repository,
                    )
                target_context[t] = ctx_entry
            result_data["target_context"] = target_context

    return result_data


def _score_decision(
    d: Any,
    query_words: set[str],
    target_files: set[str],
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
                cross_references.append(
                    {
                        "source_file": gm.file_path,
                        "sha": c.get("sha", ""),
                        "message": c.get("message", ""),
                        "author": c.get("author", ""),
                        "date": c.get("date", ""),
                        "matched_terms": matched_terms,
                    }
                )
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
    repo_path: str,
    file_path: str,
    stem: str,
) -> list[dict]:
    """Run git log against the local repo for deeper history. Best-effort."""
    import asyncio
    import subprocess

    def _sync_git_log() -> list[dict]:
        results: list[dict] = []
        try:
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
                        results.append(
                            {
                                "sha": parts[0][:12],
                                "author": parts[1],
                                "date": parts[2][:10],
                                "message": parts[3],
                                "source": "git_log_follow",
                            }
                        )

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
                            results.append(
                                {
                                    "sha": parts[0][:12],
                                    "author": parts[1],
                                    "date": parts[2][:10],
                                    "message": parts[3],
                                    "source": "git_log_grep",
                                }
                            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return results[:20]

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync_git_log), timeout=15)
    except TimeoutError:
        return []

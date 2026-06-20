"""MCP Tool 4: get_why — intent archaeology and decision search."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.decision_semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
)
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _build_origin_story,
    _compute_alignment,
    _get_exclude_spec,
    _get_repo,
    _is_path,
    _resolve_all_contexts,
    _resolve_repo_context,
    _unsupported_repo_all,
    filter_path_list,
    is_excluded,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta


@mcp.tool()
async def get_why(
    query: str | None = None,
    targets: list[str] | None = None,
    repo: str | None = None,
) -> dict:
    """Why this code is shaped this way — decision records + evidence commits.

    Call before refactors or pattern divergences. Query modes: a question
    ("why is auth using JWT?"), a file path (governing decisions + origin
    story + alignment score), a question anchored to targets, or no query
    (decision health dashboard). Falls back to git archaeology when no
    decisions exist for a path — never empty.

    Args:
        query: question, file/module path, or omit for the dashboard.
        targets: optional file paths to anchor the search.
        repo: usually omitted.
    """
    # --- repo="all": search decisions across ALL repos ---
    if repo == "all":
        if not query:
            return _unsupported_repo_all("get_why (health dashboard)")
        return await _why_workspace_search(query)

    # --- Mode 1: No query → health dashboard ---
    if not query:
        return await _why_health_dashboard(repo)

    # --- Mode 2: Path → decisions, origin story, alignment ---
    if _is_path(query):
        return await _why_path(query, repo)

    # --- Mode 3: Natural language → target-aware search ---
    return await _why_search(query, targets, repo)


async def _why_workspace_search(query: str) -> dict:
    """repo="all": keyword-search decisions across every repo in the workspace."""
    contexts = await _resolve_all_contexts()
    merged: list[dict] = []
    query_words = query.lower().split()
    for ctx in contexts:
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            res = await session.execute(
                select(DecisionRecord).where(
                    DecisionRecord.repository_id == repository.id,
                )
            )
            for d in res.scalars().all():
                text = f"{d.title} {d.decision} {d.rationale} {d.context}".lower()
                if any(w in text for w in query_words):
                    merged.append(
                        {
                            "repo": ctx.alias,
                            "id": d.id,
                            "title": d.title,
                            "status": d.status,
                            "decision": d.decision,
                            "rationale": d.rationale,
                            "source": d.source,
                            "confidence": d.confidence,
                        }
                    )
    return {
        "mode": "search",
        "query": query,
        "workspace": True,
        "decisions": merged[:15],
        "_meta": _build_meta(),
    }


async def _why_health_dashboard(repo: str | None) -> dict:
    """Mode 1: no query — return the decision health dashboard."""
    from repowise.core.persistence.crud import get_decision_health_summary

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
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
                    "affected_files": filter_path_list(
                        json.loads(d.affected_files_json), _get_exclude_spec(ctx.path)
                    )[:5],
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
            "conflicts": health.get("conflicts", [])[:10],
            "_meta": _build_meta(repository=repository),
        }


def _governing_decision_entry(d: Any, affected_files: list, lineage: list[dict]) -> dict:
    """Serialize a decision that governs a path, including its lineage chain."""
    return {
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
        "lineage": lineage if len(lineage) > 1 else [],
    }


async def _why_path(query: str, repo: str | None) -> dict:
    """Mode 2: query is a path — governing decisions, origin story, alignment."""
    ctx = await _resolve_repo_context(repo)
    if is_excluded(query, _get_exclude_spec(ctx.path)):
        return {"query": query, "error": f"'{query}' is excluded by exclude_patterns."}
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
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

        from repowise.core.persistence.decision_graph import build_lineage_chain

        governing = []
        for d in all_decisions:
            affected_files = json.loads(d.affected_files_json)
            affected_modules = json.loads(d.affected_modules_json)
            if query not in affected_files and query not in affected_modules:
                continue
            # Walk supersedes/refines back to roots so the answer is a
            # lineage chain (sessions → JWT → OAuth2), not a flat list.
            lineage = await build_lineage_chain(session, d.id)
            governing.append(_governing_decision_entry(d, affected_files, lineage))

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

        result_data["_meta"] = _build_meta(repository=repository)
        return result_data


# Stop words removed before keyword matching for better signal.
_QUERY_STOP_WORDS = {
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


async def _load_target_git(
    session: Any, repository_id: Any, targets: list[str] | None
) -> dict[str, Any]:
    """Load per-target git metadata keyed by file path (only present ones)."""
    target_git: dict[str, Any] = {}
    if not targets:
        return target_git
    for t in targets:
        git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository_id,
                GitMetadata.file_path == t,
            )
        )
        meta = git_res.scalar_one_or_none()
        if meta:
            target_git[t] = meta
    return target_git


def _rank_keyword_matches(all_decisions: list, query: str, target_set: set[str]) -> list:
    """Score decisions by weighted keyword overlap and return the top 8."""
    query_words = set(query.lower().split()) - _QUERY_STOP_WORDS
    scored_decisions: list[tuple[float, Any]] = []
    for d in all_decisions:
        score = _score_decision(d, query_words, target_set)
        if score > 0:
            scored_decisions.append((score, d))
    scored_decisions.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in scored_decisions[:8]]


async def _semantic_decision_results(ctx: Any, query: str) -> list:
    """Semantic search of the page store, filtered to the decision: namespace."""
    decision_results: list = []
    with contextlib.suppress(Exception):
        if ctx.vector_store is not None:
            _raw = await ctx.vector_store.search(query, limit=50)
            decision_results = [
                r for r in _raw if getattr(r, "page_id", "").startswith(DECISION_VECTOR_PREFIX)
            ][:5]
    return decision_results


async def _semantic_doc_results(ctx: Any, query: str) -> list:
    """Semantic search over documentation, falling back to FTS."""
    try:
        return await ctx.vector_store.search(query, limit=3)
    except Exception:
        doc_results: list = []
        with contextlib.suppress(Exception):
            doc_results = await ctx.fts.search(query, limit=3)
        return doc_results


async def _lineage_for_matches(ctx: Any, keyword_matches: list) -> dict[str, list[dict]]:
    """Walk lineage chains for the keyword matches; keep only multi-node chains."""
    from repowise.core.persistence.decision_graph import build_lineage_chain

    lineage_by_id: dict[str, list[dict]] = {}
    if keyword_matches:
        async with get_session(ctx.session_factory) as session3:
            for d in keyword_matches:
                chain = await build_lineage_chain(session3, d.id)
                if len(chain) > 1:
                    lineage_by_id[d.id] = chain
    return lineage_by_id


def _merge_decisions(
    keyword_matches: list,
    decision_results: list,
    lineage_by_id: dict[str, list[dict]],
) -> list[dict]:
    """Merge keyword and semantic decision hits, deduplicated by id."""
    seen_ids: set[str] = set()
    merged_decisions: list[dict] = []
    for d in keyword_matches:
        if d.id in seen_ids:
            continue
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
                "lineage": lineage_by_id.get(d.id, []),
            }
        )

    for r in decision_results:
        # Strip the "decision:" prefix so the returned id matches the SQL primary key.
        real_id = r.page_id[len(DECISION_VECTOR_PREFIX) :]
        if real_id in seen_ids:
            continue
        seen_ids.add(real_id)
        merged_decisions.append(
            {
                "id": real_id,
                "title": r.title,
                "snippet": r.snippet,
                "relevance_score": r.score,
            }
        )
    return merged_decisions


async def _build_target_context(
    ctx: Any,
    repository: Any,
    all_decisions: list,
    target_git: dict[str, Any],
    targets: list[str],
) -> dict[str, Any]:
    """Per-target governing decisions + origin story, with archaeology fallback."""
    async with get_session(ctx.session_factory) as session2:
        # Load all git metadata for cross-file search
        all_git_res = await session2.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
            )
        )
        all_git_meta_list = all_git_res.scalars().all()

        target_context: dict[str, Any] = {}
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
        return target_context


async def _why_search(query: str, targets: list[str] | None, repo: str | None) -> dict:
    """Mode 3: natural-language, target-aware decision + documentation search."""
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        all_decisions = await _list_decisions(
            session, repository.id, include_proposed=True, limit=200
        )
        # Load git metadata for targets (for origin context in results)
        target_git = await _load_target_git(session, repository.id, targets)

    target_set = set(targets) if targets else set()
    keyword_matches = _rank_keyword_matches(all_decisions, query, target_set)
    decision_results = await _semantic_decision_results(ctx, query)
    doc_results = await _semantic_doc_results(ctx, query)
    lineage_by_id = await _lineage_for_matches(ctx, keyword_matches)
    merged_decisions = _merge_decisions(keyword_matches, decision_results, lineage_by_id)

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
        result_data["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets
        )

    result_data["_meta"] = _build_meta(repository=repository)
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
    result["file_commits"] = file_commits[:10]  # Cap to keep response bounded

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
    if local_path and (Path(local_path) / ".git").is_dir():
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
        import re

        results: list[dict] = []
        # Sanitize stem to prevent argument injection via --grep
        safe_stem = re.sub(r"[^a-zA-Z0-9_\-.]", "", stem) if stem else ""
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

            if safe_stem and len(safe_stem) >= 3:
                proc2 = subprocess.run(
                    [
                        "git",
                        "log",
                        "--all",
                        "--grep",
                        safe_stem,
                        "--format=%H\t%an\t%ai\t%s",
                        "-10",
                        "--",  # end of options — prevent argument injection
                    ],
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

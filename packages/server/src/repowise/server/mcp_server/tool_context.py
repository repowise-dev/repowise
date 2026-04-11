"""MCP Tool 2: get_context — complete context for files, modules, or symbols."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# --- Output size budget -------------------------------------------------------
# The Claude Code harness rejects MCP tool results whose stringified form exceeds
# ~10k tokens (it refuses to inline them and then refuses to Read the spilled
# file). When that happens the agent falls back to multiple get_symbol calls,
# each of which re-plays the cached system prompt — a significant cost driver
# on dense files in long multi-turn agent sessions.
#
# We therefore cap get_context output well below that ceiling. 8000 tokens
# leaves headroom for the wrapping JSON envelope and _meta fields the harness
# adds on top. The estimator is intentionally dependency-free: 4 chars/token is
# the widely-quoted average for English + code on BPE tokenizers and is within
# ~20% of tiktoken for typical wiki content. Precise counting is unnecessary
# because we only need to stay comfortably under the hard limit.
_TOKEN_BUDGET = 8000
_CHARS_PER_TOKEN = 4
_CHAR_BUDGET = _TOKEN_BUDGET * _CHARS_PER_TOKEN

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import context_hint as _context_hint
from repowise.server.mcp_server._server import mcp


def _synthesize_structural_summary(
    file_path: str, classes: list[str], functions: list[str]
) -> str:
    """Build a deterministic 1-line summary when no LLM-generated summary exists.

    Used in --index-only mode (no wiki pages) and as a fallback when an LLM
    page predates the summary column. Always returns a non-empty string so the
    agent never sees a missing field.
    """
    name = file_path.rsplit("/", 1)[-1]
    parts: list[str] = []
    if classes:
        head = ", ".join(classes[:3])
        more = f" (+{len(classes) - 3} more)" if len(classes) > 3 else ""
        parts.append(f"defines {head}{more}")
    if functions:
        head = ", ".join(functions[:3])
        more = f" (+{len(functions) - 3} more)" if len(functions) > 3 else ""
        parts.append(f"function{'s' if len(functions) > 1 else ''} {head}{more}")
    if not parts:
        return f"{name}: empty or non-symbol file"
    return f"{name}: " + "; ".join(parts) + "."


async def _resolve_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    include: set[str] | None,
    compact: bool = False,
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
        # Fallback 1: index-only mode (no wiki pages) — return graph node + symbols if present
        res = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_id == target,
            )
        )
        gnode = res.scalar_one_or_none()
        if gnode is not None:
            target_type = "file"
            file_path_for_git = target
            page = None  # no wiki page; subsequent blocks must guard for this

        # Fallback 2: check git_metadata — file may exist but have no wiki page AND no graph node
        if target_type is None:
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

        # Fallback 3: fuzzy path suggestions — match by filename or partial path.
        # Only runs if the prior fallbacks didn't resolve the target.
        if target_type is None:
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
        want_full_doc = bool(include and "full_doc" in include)
        docs: dict[str, Any] = {}
        if target_type == "file":
            if page is not None:
                docs["title"] = page.title
                docs["summary"] = page.summary or ""
                if want_full_doc:
                    docs["content_md"] = page.content
                if page.human_notes:
                    docs["human_notes"] = page.human_notes
            # Symbols in this file
            res = await session.execute(
                select(WikiSymbol).where(
                    WikiSymbol.repository_id == repo_id,
                    WikiSymbol.file_path == target,
                )
            )
            symbols = res.scalars().all()
            classes = [s.name for s in symbols if s.kind == "class"]
            functions = [s.name for s in symbols if s.kind in ("function", "method")]
            if compact:
                # Compact mode: name+kind+signature+line only. Drop docstring,
                # start_line/end_line range, structure block, and imported_by.
                # Used by agents that already know the file and just want a
                # cheap signature index.
                docs["symbols"] = [
                    {
                        "name": s.name,
                        "kind": s.kind,
                        "signature": s.signature,
                        "line": s.start_line,
                    }
                    for s in symbols
                ]
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(
                        target, classes, functions
                    )
            else:
                docs["symbols"] = [
                    {
                        "name": s.name,
                        "kind": s.kind,
                        "signature": s.signature,
                        "start_line": s.start_line,
                        "end_line": s.end_line,
                        "docstring": (s.docstring or "")[:400],
                    }
                    for s in symbols
                ]
                # Structure summary block — quick scan of what's in the file
                total_loc = max((s.end_line for s in symbols), default=0)
                avg_complexity = (
                    sum(s.complexity_estimate for s in symbols) / len(symbols)
                    if symbols
                    else 0
                )
                docs["structure"] = {
                    "classes": classes,
                    "functions": functions,
                    "symbol_count": len(symbols),
                    "total_loc": total_loc,
                    "avg_complexity": round(avg_complexity, 2),
                }
                # Fallback summary: if no Page (index-only mode) or page.summary
                # is empty, synthesize a deterministic one-liner from structure.
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(
                        target, classes, functions
                    )
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
            docs["summary"] = page.summary or ""
            if want_full_doc:
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
            # File page summary (full content gated behind include=["full_doc"])
            sym_page_id = f"file_page:{sym.file_path}"
            sym_page = await session.get(Page, sym_page_id)
            if sym_page is not None:
                docs["file_summary"] = sym_page.summary or ""
                if want_full_doc:
                    docs["documentation"] = sym_page.content
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


def _estimate_tokens(obj: Any) -> int:
    """Cheap upper-bound token estimate for an arbitrary JSON-serialisable object.

    Serialises to compact JSON (the wire format the MCP layer eventually emits)
    and divides by ``_CHARS_PER_TOKEN``. We use the serialised form — not just
    raw text fields — because structural JSON overhead (quotes, braces, field
    names) is non-trivial and is what the downstream tokenizer actually sees.
    """
    return len(json.dumps(obj, separators=(",", ":"), default=str)) // _CHARS_PER_TOKEN


# Heavy optional fields we can strip from a target's docs block without losing
# its identity. Ordering matters: earlier entries are dropped first because they
# carry the most bytes per unit of navigational value.
_HEAVY_DOC_FIELDS: tuple[str, ...] = ("content_md", "documentation", "file_summary")


def _symbol_priority(sym: dict[str, Any], query_terms: set[str]) -> tuple[int, int, int]:
    """Return a sort key (higher = keep) for a symbol within a target.

    Priority order (language-agnostic — no Python-specific heuristics):
      1. Exact name match against any user query term.
      2. Substring / case-insensitive match against query terms.
      3. Kind rank: classes/types outrank functions/methods which outrank the
         rest. This mirrors navigational usefulness across Python, TS, Go,
         Rust, C++, etc. where a type anchors a module more than a helper fn.
      4. PageRank / centrality if present on the dict (forward-compatible —
         ``get_context`` doesn't currently populate it but ``_resolve_one_target``
         may in the future).
    """
    name = (sym.get("name") or "").lower()
    exact = 1 if name and name in query_terms else 0
    fuzzy = 1 if any(t and t in name for t in query_terms) else 0
    kind = (sym.get("kind") or "").lower()
    kind_rank = {
        "class": 3, "interface": 3, "struct": 3, "trait": 3, "type": 3, "enum": 3,
        "function": 2, "method": 2,
    }.get(kind, 1)
    centrality = int((sym.get("pagerank") or sym.get("centrality") or 0) * 1000)
    return (exact * 10 + fuzzy * 5 + kind_rank, centrality, -len(json.dumps(sym, default=str)))


def _query_terms_for(target: str) -> set[str]:
    """Derive cheap query terms from a target string for symbol prioritisation.

    ``get_context`` has no explicit query argument, so we fall back to the
    target identifier itself — the tail of a file path, or the raw symbol name.
    This is deliberately coarse: it just nudges symbol retention toward the
    thing the caller asked about.
    """
    tail = target.rsplit("/", 1)[-1].lower()
    # Strip common extension if present (language-agnostic: split once on '.').
    if "." in tail:
        tail = tail.rsplit(".", 1)[0]
    return {t for t in (tail, target.lower()) if t}


def _truncate_to_budget(
    result: dict[str, Any],
    char_budget: int = _CHAR_BUDGET,
) -> dict[str, Any]:
    """Cap the ``get_context`` response at roughly ``_TOKEN_BUDGET`` tokens.

    Strategy (applied in order, stopping as soon as the budget is met):

    1. **Strip heavy optional doc fields** (``content_md``, ``documentation``,
       ``file_summary``) from each target. These are 1–2k tokens apiece and
       duplicate information the agent can re-request via ``full_doc``.
    2. **Shrink symbol lists within each target**, keeping the highest-priority
       symbols per ``_symbol_priority``. This preserves the navigational index
       (names, signatures, line numbers) while dropping bulk docstrings.
    3. **Drop whole targets** from the tail of the list. Per spec we prefer
       keeping fewer full-fidelity targets over many stubs, so once symbols
       can't shrink further we evict entire targets rather than gutting them.

    Adds ``truncated: bool``, ``dropped_targets: list[str]``, and
    ``dropped_symbols: dict[target, list[name]]`` top-level fields — additive
    only, existing callers are unaffected.

    Edge cases:
      * Empty ``targets`` → returns unchanged with ``truncated=False``.
      * A single target whose symbol list alone busts the budget → we reduce
        symbols down to 1 and accept the overshoot rather than returning an
        empty response. The ``truncated`` flag still fires.
      * Targets that carry an ``error`` field (not-found) are cheap and are
        preserved unless literally nothing else fits.
    """
    result.setdefault("truncated", False)
    result.setdefault("dropped_targets", [])
    result.setdefault("dropped_symbols", {})

    targets: dict[str, Any] = result.get("targets") or {}
    if not targets:
        return result

    def _size() -> int:
        return len(json.dumps(result, separators=(",", ":"), default=str))

    if _size() <= char_budget:
        return result

    # Stage 1: strip heavy optional doc fields across all targets.
    for name, tgt in targets.items():
        docs = tgt.get("docs") if isinstance(tgt, dict) else None
        if not isinstance(docs, dict):
            continue
        for field in _HEAVY_DOC_FIELDS:
            if field in docs:
                docs.pop(field, None)
                result["truncated"] = True
        if _size() <= char_budget:
            return result

    # Stage 2: prioritise symbols within each target. We iterate from the
    # largest target down so the biggest offenders shrink first.
    def _target_cost(item: tuple[str, Any]) -> int:
        return len(json.dumps(item[1], default=str))

    for tgt_name, tgt in sorted(targets.items(), key=_target_cost, reverse=True):
        docs = tgt.get("docs") if isinstance(tgt, dict) else None
        if not isinstance(docs, dict):
            continue
        symbols = docs.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            continue
        query_terms = _query_terms_for(tgt_name)
        ordered = sorted(symbols, key=lambda s: _symbol_priority(s, query_terms), reverse=True)
        kept: list[dict[str, Any]] = []
        dropped: list[str] = []
        # Add symbols one at a time from highest priority until we hit the cap.
        for sym in ordered:
            docs["symbols"] = kept + [sym]
            if _size() <= char_budget:
                kept.append(sym)
            else:
                dropped.append(sym.get("name") or "<anonymous>")
        if not kept and ordered:
            # Edge case: a single symbol is larger than the budget. Keep one
            # (truncating its docstring) rather than returning zero symbols —
            # the caller at least learns the target resolved.
            head = dict(ordered[0])
            if isinstance(head.get("docstring"), str):
                head["docstring"] = head["docstring"][:200]
            kept = [head]
            dropped = [s.get("name") or "<anonymous>" for s in ordered[1:]]
        docs["symbols"] = kept
        if dropped:
            result["dropped_symbols"][tgt_name] = dropped
            result["truncated"] = True
        if _size() <= char_budget:
            return result

    # Stage 3: drop whole targets, largest first, until we fit. Prefer to keep
    # error-only targets (they're tiny and signal "not found" to the caller).
    def _evictable_order() -> list[str]:
        items = list(targets.items())
        items.sort(
            key=lambda kv: (
                0 if isinstance(kv[1], dict) and "error" in kv[1] else 1,
                len(json.dumps(kv[1], default=str)),
            ),
            reverse=True,
        )
        return [k for k, _ in items]

    for name in _evictable_order():
        if len(targets) <= 1:
            break
        targets.pop(name, None)
        result["dropped_targets"].append(name)
        result["truncated"] = True
        if _size() <= char_budget:
            break

    if result["truncated"]:
        logger.info(
            "get_context truncated to budget",
            extra={
                "char_budget": char_budget,
                "token_budget": _TOKEN_BUDGET,
                "final_chars": _size(),
                "dropped_targets": result["dropped_targets"],
                "dropped_symbol_counts": {
                    k: len(v) for k, v in result["dropped_symbols"].items()
                },
            },
        )
    return result


@mcp.tool()
async def get_context(
    targets: list[str],
    include: list[str] | None = None,
    compact: bool = True,
    repo: str | None = None,
) -> dict:
    """Get a compact navigation index for files/modules/symbols. Batch targets.

    Default returns title + summary + symbol signatures (~3× smaller than
    full mode). For full structure block + imported_by + docstrings pass
    compact=False. For the full wiki content_md pass include=["docs","full_doc"].

    Example: get_context(["src/auth/service.py", "src/auth/middleware.py"])

    Args:
        targets: file paths, module paths, or symbol names.
        include: ["docs"] (default) | add "full_doc"/"ownership"/"last_change".
        compact: default True (signatures only). False adds structure+imports+docstrings.
        repo: usually omitted.
    """
    # Default to docs + freshness when include is omitted. Freshness is
    # critical for the agent to detect stale index data.  The other blocks
    # (ownership/last_change/decisions) are 200–500 bytes each and bloat
    # every subsequent agent turn via cache replay. Callers that want them
    # must pass include explicitly.
    include_set = set(include) if include else {"docs", "freshness"}

    import time as _time
    _t0 = _time.perf_counter()
    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)

        results = await asyncio.gather(
            *[
                _resolve_one_target(session, repository, t, include_set, compact)
                for t in targets
            ]
        )

    response: dict[str, Any] = {
        "targets": {r["target"]: r for r in results},
        "_meta": _build_meta(
            timing_ms=(_time.perf_counter() - _t0) * 1000,
            hint=_context_hint(targets, compact),
        ),
    }
    # Enforce the global token cap. See ``_truncate_to_budget`` for strategy.
    return _truncate_to_budget(response)

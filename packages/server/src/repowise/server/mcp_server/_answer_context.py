"""LLM context construction for ``get_answer``.

The LLM only ever sees what we put in its prompt. This module owns *that*
text — and only that. Three responsibilities, each in its own function so
the orchestrator can swap individual layers without re-deriving the rest:

  1. ``fetch_relevant_decisions`` — for "why"-shaped questions, pull
     DecisionRecord rows that touch the top hits. Architectural rationale
     lives in ADRs, not file summaries — without this layer the LLM hedges
     on every "why" question even when ``get_why`` would have the answer.

  2. ``build_structured_prelude`` — a short scaffolding block placed BEFORE
     the file excerpts: top symbols by PageRank, recent significant commits,
     decision titles, co-change partners. Gives the LLM a frame to interpret
     the excerpts against, rather than reading them cold.

  3. ``build_context_block`` — assembles the final prompt body: prelude
     followed by per-hit excerpts with their hydrated symbols. Lives here
     so the formatting is colocated with the data sources that feed it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionRecord, GitMetadata

# Heuristic for "why"-shaped questions. Cheap — first-word check plus
# "why" appearing as a whole word. Conservative so we don't mistakenly
# fuse ADRs into a "how does X work" question (which has its own grounding
# needs and doesn't want ADR-driven hedging).
_WHY_PATTERN = re.compile(r"\bwhy\b", re.IGNORECASE)

# How many decision records to inject, and per-record truncation. Three is
# usually enough — most files are governed by 0–2 active ADRs; 3 gives room
# for one tangential overlap without blowing the context budget.
_MAX_DECISIONS_INJECTED = 3
_DECISION_RATIONALE_CHARS = 400

# How many significant commits to surface in the prelude. Recent + filtered
# already by the indexer — we just take the head of the list per hit.
_MAX_PRELUDE_COMMITS_PER_HIT = 2

# Per-symbol truncation in the context block. Matches the existing
# get_answer behavior — separate constants here so future tuning doesn't
# have to thread through the orchestrator.
_MAX_SYMBOL_DOC_CHARS = 120
_MATCHED_SYMBOL_DOC_CHARS = 400
_MAX_CHARS_PER_HIT_SUMMARY = 800


def is_why_question(question: str) -> bool:
    """True when the question is about rationale, not mechanism.

    "why" present as a whole word is the signal. We avoid substring matches
    ("typewriter" contains "why" — no, but the principle holds for less
    obvious cases) by anchoring to word boundaries.
    """
    return bool(question and _WHY_PATTERN.search(question))


# ---------------------------------------------------------------------------
# Decision-record fusion
# ---------------------------------------------------------------------------


async def fetch_relevant_decisions(
    ctx: Any,
    repo_id: str,
    target_paths: list[str],
    *,
    limit: int = _MAX_DECISIONS_INJECTED,
) -> list[dict]:
    """Return up to ``limit`` decisions touching any of ``target_paths``.

    Scans active+proposed decisions (skips deprecated/superseded — those are
    historical and shouldn't drive a current answer). Ranks by overlap with
    target_paths: a decision touching three of the top hits is more relevant
    than one touching only the fifth. Ties broken by status preference
    (active > proposed) and confidence.
    """
    if not target_paths:
        return []
    targets_set = set(target_paths)
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(DecisionRecord).where(
                DecisionRecord.repository_id == repo_id,
                DecisionRecord.status.in_(("active", "proposed")),
            )
        )
        all_decisions = list(res.scalars().all())

    scored: list[tuple[int, float, DecisionRecord]] = []
    for d in all_decisions:
        try:
            affected = set(json.loads(d.affected_files_json or "[]"))
        except (json.JSONDecodeError, TypeError):
            affected = set()
        overlap = len(affected & targets_set)
        if overlap == 0:
            continue
        # Active outranks proposed at the same overlap; confidence breaks
        # ties within a status. Negative tuple element so higher = better
        # after ascending sort.
        status_rank = 0 if d.status == "active" else 1
        scored.append((-overlap, status_rank, d))

    scored.sort(key=lambda t: (t[0], t[1], -(t[2].confidence or 0.0)))
    selected = [d for _, _, d in scored[:limit]]
    return [_decision_to_dict(d) for d in selected]


def _decision_to_dict(d: DecisionRecord) -> dict:
    """Compact projection — only fields the LLM needs to ground rationale."""
    rationale = (d.rationale or "").strip()
    if len(rationale) > _DECISION_RATIONALE_CHARS:
        rationale = rationale[:_DECISION_RATIONALE_CHARS].rstrip() + "…"
    return {
        "title": d.title,
        "status": d.status,
        "decision": (d.decision or "").strip(),
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# Structured prelude
# ---------------------------------------------------------------------------


async def build_structured_prelude(
    hits: list[dict],
    decisions: list[dict],
    ctx: Any,
    repo_id: str,
) -> str:
    """Build the scaffolding block placed before file excerpts.

    Layout (each section omitted when empty so the prelude is never just
    section headers):

        Top symbols by relevance: ...
        Recent significant commits: ...
        Decision records: ...

    Designed to fit in roughly 400-800 chars regardless of how many hits we
    pass in. The point is *frame*, not coverage — the file excerpts that
    follow carry the actual material.
    """
    sections: list[str] = []

    top_symbols = _top_symbols_summary(hits)
    if top_symbols:
        sections.append(f"Top symbols by relevance: {top_symbols}")

    commits_line = await _recent_commits_summary(hits, ctx, repo_id)
    if commits_line:
        sections.append(f"Recent significant commits: {commits_line}")

    if decisions:
        titles = "; ".join(
            f"{d['title']} ({d['status']})" for d in decisions
        )
        sections.append(f"Decision records touching these files: {titles}")

    if not sections:
        return ""
    return "## Context scaffolding\n\n" + "\n".join(f"- {s}" for s in sections) + "\n"


def _top_symbols_summary(hits: list[dict]) -> str:
    """One-line summary of the top question-matched symbols across hits.

    Prefers symbols flagged ``_matched`` by upstream symbol hydration —
    those are the ones the question explicitly named. Falls back to the
    first symbol of the top hit when no matches are present.
    """
    matched: list[str] = []
    for h in hits[:3]:
        for s in h.get("symbols") or []:
            if s.get("_matched"):
                name = s.get("name") or s.get("signature") or ""
                kind = s.get("kind") or "?"
                if name:
                    matched.append(f"{name} ({kind}) in {h.get('target_path','')}")
            if len(matched) >= 4:
                break
        if len(matched) >= 4:
            break
    if matched:
        return "; ".join(matched)

    # No matched symbols — surface the head symbol of the top hit so the
    # LLM at least knows what's in the most relevant file.
    if hits and hits[0].get("symbols"):
        s = hits[0]["symbols"][0]
        name = s.get("name") or ""
        kind = s.get("kind") or ""
        path = hits[0].get("target_path", "")
        if name:
            return f"{name} ({kind}) in {path}"
    return ""


async def _recent_commits_summary(
    hits: list[dict], ctx: Any, repo_id: str
) -> str:
    """Short summary of significant commits across the top hits.

    GitMetadata.significant_commits_json is already filtered by the indexer
    for noise (style/chore commits dropped). We surface the most recent
    couple per hit, deduplicated by SHA — enough to anchor "what changed
    recently" follow-ups without rebuilding the git log.
    """
    paths = [h.get("target_path") for h in hits[:3] if h.get("target_path")]
    if not paths:
        return ""
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(GitMetadata.file_path, GitMetadata.significant_commits_json).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path.in_(paths),
            )
        )
        rows = list(res.all())

    seen_shas: set[str] = set()
    lines: list[str] = []
    for _path, sig_json in rows:
        try:
            commits = json.loads(sig_json or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for c in commits[:_MAX_PRELUDE_COMMITS_PER_HIT]:
            sha = (c.get("sha") or "")[:7]
            if sha in seen_shas or not sha:
                continue
            seen_shas.add(sha)
            msg = (c.get("message") or "").split("\n")[0][:80]
            date = (c.get("date") or "")[:10]
            lines.append(f"{sha} {date} {msg}")
            if len(lines) >= 4:
                break
        if len(lines) >= 4:
            break
    return "; ".join(lines)


# ---------------------------------------------------------------------------
# Context block assembly
# ---------------------------------------------------------------------------


def build_context_block(
    hits: list[dict],
    prelude: str = "",
    decisions: list[dict] | None = None,
    *,
    max_chars_per_hit: int = _MAX_CHARS_PER_HIT_SUMMARY,
) -> str:
    """Format the LLM prompt body: prelude + decisions block + per-hit excerpts.

    Replaces the inline ``_build_context_block`` in tool_answer. Behavior
    parity for the per-hit part (file summary, symbol signatures, matched-
    symbol source bodies); additions are the prelude and the optional
    decisions block, each gated on having content.
    """
    parts: list[str] = []
    if prelude:
        parts.append(prelude.rstrip())
    if decisions:
        parts.append(_format_decisions_block(decisions))
    parts.append(_format_hits_block(hits, max_chars_per_hit))
    return "\n\n".join(p for p in parts if p)


def _format_decisions_block(decisions: list[dict]) -> str:
    """Pull the ADRs to the front of the prompt where the LLM grounds intent."""
    lines = ["## Architectural decisions touching these files"]
    for i, d in enumerate(decisions, start=1):
        lines.append(f"[D{i}] {d['title']} ({d['status']})")
        if d.get("decision"):
            lines.append(f"    decision: {d['decision']}")
        if d.get("rationale"):
            lines.append(f"    rationale: {d['rationale']}")
    return "\n".join(lines)


def _format_hits_block(hits: list[dict], max_chars_per_hit: int) -> str:
    """Format the per-hit excerpts. Mirrors the prior tool_answer behaviour."""
    parts: list[str] = []
    for i, h in enumerate(hits, start=1):
        body_src = h.get("summary") or h.get("snippet") or ""
        body = body_src[:max_chars_per_hit]
        # Tag expanded hits so the LLM knows they didn't surface in retrieval
        # directly — useful context when deciding how much weight to put on
        # them in the answer.
        tag = " [graph-expanded]" if h.get("_expanded_from") else ""
        sources = h.get("_sources") or set()
        src_tag = f" [{'+'.join(sorted(sources))}]" if sources else ""
        block = [
            f"[{i}] {h.get('target_path','')} (score={h.get('score', 0.0):.3f}){tag}{src_tag}",
            f"    title: {h.get('title','')}",
            f"    summary: {body}",
        ]
        symbols = h.get("symbols") or []
        if symbols:
            block.append("    symbols:")
            for s in symbols:
                sig = s.get("signature") or s.get("name") or ""
                kind = s.get("kind") or "?"
                matched = bool(s.get("_matched"))
                doc = (s.get("docstring") or "").strip()
                doc_cap = _MATCHED_SYMBOL_DOC_CHARS if matched else _MAX_SYMBOL_DOC_CHARS
                match_tag = " [question-match]" if matched else ""
                block.append(f"      - [{kind}]{match_tag} {sig}")
                if doc:
                    trimmed = " ".join(doc.split())[:doc_cap]
                    block.append(f"          docstring: {trimmed}")
                src = s.get("source_excerpt")
                if src:
                    block.append("          source:")
                    for line in src.splitlines():
                        block.append(f"              {line}")
        parts.append("\n".join(block))
    return "\n\n".join(parts)

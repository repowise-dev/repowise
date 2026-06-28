"""In-code rationale mining — recover the "why" that lives in source comments.

The decision layer (ADRs / decision records) and the wiki page corpus only
capture rationale someone wrote DOWN as a decision. A large share of real
intent lives in ordinary code comments instead:

    # We retry on 429 here rather than in the client because the client
    # is shared across tenants and a global backoff would starve everyone.

`get_why` and `get_answer` both miss that: get_why searches decisions + git
archaeology, get_answer retrieves over wiki pages. Neither reads the source
comments. The unbiased A/B (task T4) confirmed the gap — when the rationale
was a code comment, get_answer returned low confidence and the agent fell
back to Read+Grep, losing on tokens AND round-trips.

This module is the **query-time recall backstop**. The durable fix is the
index-time decision harvest (Source 8,
:meth:`repowise.core.analysis.decisions.extractor.DecisionExtractor.harvest_rationale_comments`)
which lands these comments in the queryable decision corpus. This miner only
earns its keep for what the index can't cover: files edited since the last
index, and comment shapes the index-time gate drops (trailing inline comments,
intent-only markers, docstrings). The comment tokenizer and the rationale
marker list are shared with that harvest — both import from
:mod:`repowise.core.analysis.decisions.rationale_comments` so the two paths
never drift.

It is deliberately wired only into the LOW-confidence exits (get_answer's
gated / hedged paths, get_why's no-decision fallback): when the confident
corpus already answered, mining source comments buys nothing and only bloats
the payload. Reads are bounded and per-file failures are swallowed — this can
never break a tool response.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from repowise.core.analysis.decisions.rationale_comments import (
    RATIONALE_MARKERS,
    extract_comment_blocks,
)

_log = logging.getLogger("repowise.mcp.code_rationale")

# Stopwords stripped from the question before term overlap. Short, generic
# interrogatives that would match almost any comment.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "why",
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "for",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "it",
        "its",
        "and",
        "or",
        "but",
        "with",
        "from",
        "into",
        "we",
        "you",
        "use",
        "used",
        "uses",
        "using",
        "work",
        "works",
        "code",
        "get",
        "set",
        "here",
        "there",
        "than",
        "then",
        "so",
        "as",
        "if",
        "not",
        "no",
    }
)

# Bounds — best-effort means cheap. Never scan a giant generated file, never
# return a wall of text.
_MAX_FILES = 6
_MAX_FILE_LINES = 8000
_MAX_BLOCK_LINES = 12
_MAX_BLOCK_CHARS = 800
_MAX_RESULTS = 6
_NEAR_LINE_WINDOW = 60


def _content_terms(query: str | None) -> set[str]:
    """Lowercased alnum tokens from the query, stopwords + len<3 dropped."""
    if not query:
        return set()
    raw = re.findall(r"[a-zA-Z0-9_]+", query.lower())
    return {t for t in raw if len(t) >= 3 and t not in _STOPWORDS}


def _score_block(comment: str, terms: set[str]) -> tuple[float, list[str], bool]:
    """Score a comment block. Returns (score, matched_terms, has_marker).

    Rationale marker = 2.0; each distinct query term present = 1.0. Uses the
    broad ``RATIONALE_MARKERS`` set (intent + causal) shared with the index-time
    harvest — recall mode, so intent-only markers ("never", "always") count.
    """
    low = comment.lower()
    has_marker = any(m in low for m in RATIONALE_MARKERS)
    matched = sorted(t for t in terms if t in low)
    score = (2.0 if has_marker else 0.0) + float(len(matched))
    return score, matched, has_marker


def _keep(score: float, matched: list[str], has_marker: bool, has_terms: bool) -> bool:
    """Surfacing gate. With query terms: need a marker+term overlap, or a
    strong (>=2 terms) overlap on its own. Without query terms (path-mode
    "why is this file shaped this way"): a rationale marker is enough."""
    if not has_terms:
        return has_marker
    if has_marker and matched:
        return True
    return len(matched) >= 2


def _read_text(repo_root: Path, file_path: str) -> str | None:
    """Read a repo file's live text, refusing paths outside the root."""
    try:
        abs_path = (repo_root / file_path).resolve()
        abs_path.relative_to(repo_root.resolve())
    except (ValueError, OSError):
        return None
    try:
        if abs_path.stat().st_size > _MAX_FILE_LINES * 400:
            return None
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _truncate_block(comment: str) -> str:
    """Bound a single surfaced block so a long docstring can't flood the
    payload."""
    if len(comment) > _MAX_BLOCK_CHARS:
        return comment[: _MAX_BLOCK_CHARS - 1].rstrip() + "…"
    return comment


def mine_rationale(
    repo_root: Any,
    file_paths: list[str],
    query: str | None,
    *,
    near_lines: dict[str, int] | None = None,
    max_files: int = _MAX_FILES,
    max_results: int = _MAX_RESULTS,
) -> list[dict]:
    """Mine in-code rationale comments from ``file_paths``.

    Args:
        repo_root: the repo root (ctx.path); when falsy, returns [].
        file_paths: already-relevant files to scan (deduped, capped).
        query: the question / context terms to overlap against. May be None
            (path-mode), in which case marker-bearing comments are returned.
        near_lines: optional {path: line} to boost comments near an anchored
            symbol (e.g. the definition the question named).

    Returns a ranked list of ``{path, lines: [start, end], comment,
    matched_terms}`` — at most ``max_results``. Best-effort: never raises.

    Recall mode: docstrings (``kind == "doc"``) and trailing inline comments
    are kept (``include_trailing=True``), using the broad ``RATIONALE_MARKERS``
    set. The index-time harvest deliberately drops both — this miner is the
    backstop for exactly that material.
    """
    if not repo_root or not file_paths:
        return []
    try:
        root = Path(str(repo_root))
    except Exception:
        return []

    terms = _content_terms(query)
    has_terms = bool(terms)
    near_lines = near_lines or {}

    # Dedupe while preserving order; cap the file fan-out.
    seen: set[str] = set()
    ordered: list[str] = []
    for p in file_paths:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    ordered = ordered[:max_files]

    scored: list[tuple[float, bool, dict]] = []
    for path in ordered:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        text = _read_text(root, path)
        if text is None or text.count("\n") > _MAX_FILE_LINES:
            continue
        near = near_lines.get(path)
        try:
            blocks = extract_comment_blocks(text, ext, include_trailing=True)
        except Exception as exc:  # never let a tokenizer bug break a tool
            _log.debug("comment extraction failed for %s: %s", path, exc)
            continue
        for block in blocks:
            start, end, comment = block.start_line, block.end_line, block.text
            score, matched, has_marker = _score_block(comment, terms)
            if not _keep(score, matched, has_marker, has_terms):
                continue
            if near is not None and abs(start - near) <= _NEAR_LINE_WINDOW:
                score += 1.5
            # Coalesced runs longer than the cap are split at the head — the
            # lead lines carry the rationale; tail is usually elaboration.
            if end - start + 1 > _MAX_BLOCK_LINES:
                end = start + _MAX_BLOCK_LINES - 1
            scored.append(
                (
                    score,
                    has_marker,
                    {
                        "path": path,
                        "lines": [start, end],
                        "comment": _truncate_block(comment),
                        "matched_terms": matched,
                    },
                )
            )

    # Precision: a comment with an explicit rationale marker IS the "why". When
    # any survive, drop the marker-less blocks that only cleared the >=2-term
    # gate — on a query with generic terms (lines / source / one) those are
    # usually plain docstrings that read as noise next to the real rationale.
    # The term-only blocks remain the recall fallback when nothing has a marker.
    if any(m for _, m, _ in scored):
        scored = [t for t in scored if t[1]]

    scored.sort(key=lambda t: t[0], reverse=True)
    return [entry for _, _, entry in scored[:max_results]]

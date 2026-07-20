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

This module is the **query-time miner** that closes the gap, and since the
removal of the index-time ``code_comment`` harvest (#751: it duplicated this
miner while flooding the proposed-decision queue) it is the only consumer of
the shared heuristics in
:mod:`repowise.core.analysis.decisions.rationale_comments`. Mining live means
the served comments are always fresh, including files edited since the last
index.

It is deliberately wired only into the LOW-confidence exits (get_answer's
gated / hedged paths, get_why's no-decision fallback): when the confident
corpus already answered, mining source comments buys nothing and only bloats
the payload. Reads are bounded and per-file failures are swallowed — this can
never break a tool response.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from repowise.core.analysis.decisions.rationale_comments import (
    RATIONALE_MARKERS,
    extract_comment_blocks,
)
from repowise.core.exclusion import build_exclude_spec, is_excluded

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
    """Lowercased alnum tokens from the query, stopwords + len<3 dropped.

    Drops len<3 tokens, so a bare number like ``40`` never survives here - that
    is deliberate (a stray digit is a poor term), and is exactly why the literal
    number is scored separately via :func:`_salient_numbers`.
    """
    if not query:
        return set()
    raw = re.findall(r"[a-zA-Z0-9_]+", query.lower())
    return {t for t in raw if len(t) >= 3 and t not in _STOPWORDS}


# A literal number the question pins down (a cap / limit / count / version).
# ``_content_terms`` drops it (len<3, non-alpha), but the number is often the
# WHOLE point - "why is X capped at 40" vs "...at 600" can only be told apart by
# the digit. So it is scored separately, and heavily.
_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w)")


def _salient_numbers(query: str | None) -> list[str]:
    """Literal numbers named in the query, as verbatim tokens (e.g. ``40``)."""
    if not query:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for n in _NUMBER_RE.findall(query):
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _number_in_text(num: str, low: str) -> bool:
    """Whole-token match for a number in lowercased text - ``40`` matches
    ``cap at 40`` but not ``400`` or ``0.40``."""
    return re.search(rf"(?<![\w.]){re.escape(num)}(?![\w.])", low) is not None


def _score_block(
    comment: str, terms: set[str], numbers: list[str]
) -> tuple[float, list[str], list[str], bool]:
    """Score a comment block. Returns (score, matched_terms, matched_nums, has_marker).

    Rationale marker = 2.0; each distinct query term present = 1.0; each literal
    question number present = 3.0 (the strongest single signal - it identifies
    the exact constant the question asks about, cutting through cap/limit
    boilerplate). Uses the broad ``RATIONALE_MARKERS`` set (intent + causal)
    shared with the index-time harvest - recall mode, so intent-only markers
    ("never", "always") count.
    """
    low = comment.lower()
    has_marker = any(m in low for m in RATIONALE_MARKERS)
    matched = sorted(t for t in terms if t in low)
    matched_nums = [n for n in numbers if _number_in_text(n, low)]
    score = (2.0 if has_marker else 0.0) + float(len(matched)) + 3.0 * len(matched_nums)
    return score, matched, matched_nums, has_marker


def _keep(
    matched: list[str],
    matched_nums: list[str],
    has_marker: bool,
    has_terms: bool,
) -> bool:
    """Surfacing gate. A pinned question number co-occurring with a marker or
    any content term is kept (the number disambiguates the exact constant).
    With query terms: need a marker+term overlap, or a strong (>=2 terms)
    overlap on its own. Without query terms (path-mode "why is this file shaped
    this way"): a rationale marker is enough."""
    if matched_nums and (has_marker or matched):
        return True
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
    numbers = _salient_numbers(query)
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

    scored: list[tuple[float, bool, bool, dict]] = []
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
            score, matched, matched_nums, has_marker = _score_block(comment, terms, numbers)
            if not _keep(matched, matched_nums, has_marker, has_terms):
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
                    bool(matched_nums),
                    {
                        "path": path,
                        "lines": [start, end],
                        "comment": _truncate_block(comment),
                        "matched_terms": matched + matched_nums,
                    },
                )
            )

    # Precision: a comment with an explicit rationale marker IS the "why". When
    # any survive, drop the marker-less blocks that only cleared the >=2-term
    # gate — on a query with generic terms (lines / source / one) those are
    # usually plain docstrings that read as noise next to the real rationale.
    # The term-only blocks remain the recall fallback when nothing has a marker;
    # a block pinned by a literal question number is always kept (it identifies
    # the exact constant, marker or not).
    if any(m for _, m, _, _ in scored):
        scored = [t for t in scored if t[1] or t[2]]

    scored.sort(key=lambda t: t[0], reverse=True)
    return [entry for _, _, _, entry in scored[:max_results]]


# Cap / limit vocabulary: a question may say "capped" while the source comment
# says "limit" (or vice versa). Treat the family as interchangeable anchors so a
# number-free "why is X limited" question still has something to grep on.
_CAP_FAMILY: tuple[str, ...] = ("cap", "limit", "max", "bound", "ceiling", "threshold")

# Git-grep is bounded so a pathological pattern can't stall a tool response.
_GREP_TIMEOUT_S = 20


def grep_comment_candidates(
    repo_root: Any,
    query: str,
    *,
    max_files: int = _MAX_FILES,
) -> list[str]:
    """Git-grep COMMENT lines for the question's number(s) + content nouns.

    Concept anchoring's discriminator. Fuzzy retrieval misses the file where a
    number-bearing behaviour is *explained* ("why is the caller list capped at
    50" landed on tool_symbol.py, never enrichment.py). A boilerplate call site
    (``limit = 50``) carries no comment; the file that explains the choice has a
    rationale comment mentioning the number + the thing it bounds. So we grep
    comment-leading lines (``#`` / ``//`` / ``--`` / ``*``) that carry a salient
    number (or, number-free, a cap-family word) AND a content noun, then rank
    files by how many such lines they hold.

    Returns a ranked, deduped list of repo-relative paths (most matches first),
    capped at ``max_files``. Best-effort: ``[]`` on any failure, never raises.
    The winning files are meant to be fed straight into :func:`mine_rationale`.
    """
    try:
        root = Path(str(repo_root))
    except Exception:
        return []
    if not repo_root:
        return []
    numbers = _salient_numbers(query)
    terms = _content_terms(query)
    nouns = [t for t in terms if t not in _CAP_FAMILY]
    anchors = list(numbers) or list(_CAP_FAMILY)
    if not nouns or not anchors:
        return []
    noun_alt = "|".join(re.escape(n) for n in nouns)
    anchor_alt = "|".join(re.escape(a) for a in anchors)
    # A comment-leading line containing an anchor AND a content noun, in either
    # order. ``git grep`` keeps the scan inside tracked files only. Use the
    # POSIX class ``[[:space:]]`` instead of ``\s``: ``\s`` is a GNU regex
    # extension that git's ERE engine does not honor on macOS/BSD, so the
    # pattern would silently match nothing there (and concept-anchoring would
    # quietly disable itself).
    pat = (
        rf"^[[:space:]]*(#|//|--|\*).*({anchor_alt}).*({noun_alt})"
        rf"|^[[:space:]]*(#|//|--|\*).*({noun_alt}).*({anchor_alt})"
    )
    try:
        proc = subprocess.run(
            # --no-pager + stdin=DEVNULL are load-bearing, not cosmetic: this can
            # run inside a stdio MCP server whose stdin IS the JSON-RPC pipe.
            # subprocess.run does NOT redirect stdin by default, so a pager / hook
            # that reads stdin would consume the protocol stream and deadlock the
            # whole server. Redirect it to /dev/null and disable the pager.
            [
                "git",
                "--no-pager",
                "grep",
                # --no-color: this output is parsed, so a user's
                # ``color.ui=always`` git config must not wrap paths in ANSI
                # escapes (which would corrupt the path split below).
                "--no-color",
                "-n",
                "-I",
                "-i",
                "-E",
                pat,
                "--",
                "*.py",
                "*.ts",
                "*.tsx",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_GREP_TIMEOUT_S,
        )
    except Exception:
        return []
    counts: Counter[str] = Counter()
    for line in proc.stdout.splitlines():
        path = line.split(":", 1)[0]
        if path:
            counts[path] += 1
    # ``git grep`` only scans tracked files, but a gitignored copy can still be
    # tracked (or land here via a future --no-index retry); filter the winners
    # through the repo's exclusion rules so an ignored path is never anchored on.
    spec = build_exclude_spec(root)
    ranked = [p for p, _ in counts.most_common() if not is_excluded(p, spec)]
    return ranked[:max_files]

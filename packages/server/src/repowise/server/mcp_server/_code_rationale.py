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

This module closes that gap with a *best-effort* miner (same spirit as the
git-archaeology fallback in tool_why): given a handful of already-relevant
files and the question terms, scan their comment blocks for ones that carry a
rationale marker ("because", "instead of", "workaround", "to avoid", "HACK",
"NOTE", …) overlapping the question, and return them as grounded
``{path, lines, comment}`` evidence the agent can cite without a Read.

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

_log = logging.getLogger("repowise.mcp.code_rationale")

# --- Rationale markers --------------------------------------------------------
# A comment earns surfacing only if it reads like a *reason*, not a label.
# These are the causal / intent connectives and the well-known intent tags.
# Matched as lowercased substrings against the comment text. Curated to favor
# precision: a URL or a boilerplate header won't contain "because"/"instead".
_RATIONALE_MARKERS: tuple[str, ...] = (
    "because",
    "instead of",
    "rather than",
    "to avoid",
    "avoids ",
    "avoid ",
    "work around",
    "workaround",
    "so that",
    "otherwise",
    "due to",
    "to prevent",
    "prevents ",
    "the reason",
    "reason:",
    "intentional",
    "intentionally",
    "deliberate",
    "deliberately",
    "on purpose",
    "we use ",
    "we don't",
    "we do not",
    "we can't",
    "we cannot",
    "must not",
    "never ",
    "always ",
    "fall back",
    "fallback",
    "legacy",
    "historical",
    "backward compat",
    "backwards compat",
    "for performance",
    "perf:",
    "hot path",
    "deadlock",
    "race condition",
    "thread-safe",
    "thread safe",
    "this is why",
    "the trick",
    "subtle",
    "tricky",
    "gotcha",
    "caveat",
    "hack",
    "fixme",
    "xxx:",
    "note:",
    "nb:",
    "important:",
    "warning:",
    "defense in depth",
    "edge case",
    "do not remove",
    "don't remove",
)

# --- Comment syntax per file extension ---------------------------------------
# Line-comment prefixes keyed by extension. Kept pragmatic: covers the
# languages that make up the overwhelming majority of indexed source.
_LINE_PREFIXES: dict[str, tuple[str, ...]] = {
    "py": ("#",),
    "pyi": ("#",),
    "sh": ("#",),
    "bash": ("#",),
    "rb": ("#",),
    "yaml": ("#",),
    "yml": ("#",),
    "toml": ("#",),
    "r": ("#",),
    "pl": ("#",),
    "js": ("//",),
    "jsx": ("//",),
    "ts": ("//",),
    "tsx": ("//",),
    "mjs": ("//",),
    "cjs": ("//",),
    "go": ("//",),
    "rs": ("//",),
    "c": ("//",),
    "h": ("//",),
    "cc": ("//",),
    "cpp": ("//",),
    "hpp": ("//",),
    "java": ("//",),
    "kt": ("//",),
    "kts": ("//",),
    "swift": ("//",),
    "scala": ("//",),
    "cs": ("//",),
    "php": ("//", "#"),
    "dart": ("//",),
    "sql": ("--",),
    "lua": ("--",),
    "hs": ("--",),
    "elm": ("--",),
}

# Extensions that use C-style /* ... */ block comments.
_C_BLOCK_EXTS: frozenset[str] = frozenset(
    {
        "js",
        "jsx",
        "ts",
        "tsx",
        "mjs",
        "cjs",
        "go",
        "rs",
        "c",
        "h",
        "cc",
        "cpp",
        "hpp",
        "java",
        "kt",
        "kts",
        "swift",
        "scala",
        "cs",
        "php",
        "dart",
    }
)

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


def _strip_comment_open(stripped: str, prefixes: tuple[str, ...]) -> str | None:
    """If ``stripped`` begins with a line-comment prefix, return the text
    after it; else None. Handles ``# x``, ``// x``, ``-- x`` and bare ``#``."""
    for p in prefixes:
        if stripped.startswith(p):
            return stripped[len(p) :].lstrip()
    return None


def _trailing_comment(line: str, prefixes: tuple[str, ...]) -> str | None:
    """Recover a trailing inline comment (``x = 1  # because ...``).

    Conservative: requires whitespace before the marker so we don't trip on
    ``//`` inside a URL or ``#`` inside a string literal. The downstream
    rationale-marker gate is the real safety net — a comment with no causal
    word is dropped regardless.
    """
    best: str | None = None
    best_idx = len(line) + 1
    for p in prefixes:
        idx = line.find(" " + p + " ")
        if idx == -1:
            idx = line.find("\t" + p)
        if idx != -1 and idx < best_idx:
            # Skip if the whole line is already a full-line comment (handled
            # elsewhere) — i.e. there is non-comment text before the marker.
            head = line[:idx].strip()
            if head:
                best_idx = idx
                best = line[idx:].lstrip()[len(p) :].lstrip()
    return best


def extract_comment_blocks(text: str, ext: str) -> list[tuple[int, int, str]]:
    """Return ``(start_line, end_line, comment_text)`` for every comment block.

    Consecutive full-line comments coalesce into one block (a multi-line
    rationale paragraph stays intact). Also recovers C-style ``/* */`` blocks,
    Python triple-quoted docstrings, and trailing inline comments. 1-indexed,
    inclusive. Best-effort tokenizing — not a real parser.
    """
    prefixes = _LINE_PREFIXES.get(ext, ())
    lines = text.splitlines()
    blocks: list[tuple[int, int, str]] = []

    cur_start = 0
    cur_parts: list[str] = []

    def _flush() -> None:
        nonlocal cur_start, cur_parts
        if cur_parts:
            joined = " ".join(p for p in cur_parts if p).strip()
            if joined:
                blocks.append((cur_start, cur_start + len(cur_parts) - 1, joined))
        cur_start = 0
        cur_parts = []

    in_c_block = False
    in_pydoc = False
    pydoc_delim = ""
    block_start = 0
    block_parts: list[str] = []
    has_c_block = ext in _C_BLOCK_EXTS
    has_pydoc = ext in ("py", "pyi")

    for i, raw in enumerate(lines, start=1):
        stripped = raw.strip()

        # --- inside a /* ... */ block ---
        if in_c_block:
            end = stripped.find("*/")
            seg = stripped[: end if end != -1 else len(stripped)]
            block_parts.append(seg.lstrip("*").strip())
            if end != -1:
                in_c_block = False
                joined = " ".join(p for p in block_parts if p).strip()
                if joined:
                    blocks.append((block_start, i, joined))
                block_parts = []
            continue

        # --- inside a python docstring ---
        if in_pydoc:
            end = stripped.find(pydoc_delim)
            seg = stripped[: end if end != -1 else len(stripped)]
            block_parts.append(seg.strip())
            if end != -1:
                in_pydoc = False
                joined = " ".join(p for p in block_parts if p).strip()
                if joined:
                    blocks.append((block_start, i, joined))
                block_parts = []
            continue

        # --- full-line comment ---
        body = _strip_comment_open(stripped, prefixes) if prefixes else None
        if body is not None:
            if not cur_parts:
                cur_start = i
            cur_parts.append(body)
            continue

        # not a full-line comment → flush any pending run
        _flush()

        # --- start of a /* ... */ block ---
        if has_c_block and "/*" in stripped:
            open_idx = stripped.find("/*")
            close_idx = stripped.find("*/", open_idx + 2)
            if close_idx != -1:
                seg = stripped[open_idx + 2 : close_idx].strip()
                if seg:
                    blocks.append((i, i, seg))
            else:
                in_c_block = True
                block_start = i
                block_parts = [stripped[open_idx + 2 :].lstrip("*").strip()]
            continue

        # --- start of a python docstring (standalone, e.g. module/class doc) ---
        if has_pydoc:
            m = re.match(r'^[rbRB]?("""|\'\'\')', stripped)
            if m:
                pydoc_delim = m.group(1)
                rest = stripped[m.end() :]
                close = rest.find(pydoc_delim)
                if close != -1:
                    seg = rest[:close].strip()
                    if seg:
                        blocks.append((i, i, seg))
                else:
                    in_pydoc = True
                    block_start = i
                    block_parts = [rest.strip()]
                continue

        # --- trailing inline comment ---
        if prefixes:
            tail = _trailing_comment(raw, prefixes)
            if tail:
                blocks.append((i, i, tail))

    _flush()
    return blocks


def _score_block(comment: str, terms: set[str]) -> tuple[float, list[str], bool]:
    """Score a comment block. Returns (score, matched_terms, has_marker).

    Rationale marker = 2.0; each distinct query term present = 1.0.
    """
    low = comment.lower()
    has_marker = any(m in low for m in _RATIONALE_MARKERS)
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

    scored: list[tuple[float, dict]] = []
    for path in ordered:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        text = _read_text(root, path)
        if text is None or text.count("\n") > _MAX_FILE_LINES:
            continue
        near = near_lines.get(path)
        try:
            blocks = extract_comment_blocks(text, ext)
        except Exception as exc:  # never let a tokenizer bug break a tool
            _log.debug("comment extraction failed for %s: %s", path, exc)
            continue
        for start, end, comment in blocks:
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
                    {
                        "path": path,
                        "lines": [start, end],
                        "comment": _truncate_block(comment),
                        "matched_terms": matched,
                    },
                )
            )

    scored.sort(key=lambda t: t[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]

"""Rationale-comment mining — the shared heuristics for recovering the "why"
that lives in ordinary source comments rather than in an ADR / decision record.

A large share of real architectural intent never becomes a decision record; it
sits in a code comment instead::

    # We retry on 429 here rather than in the client because the client is
    # shared across tenants and a global backoff would starve everyone.

Two consumers want exactly the same comment-extraction + rationale-marker
heuristics, so they live here once:

* the **index-time** decision-extractor pass
  (:meth:`repowise.core.analysis.decisions.extractor.DecisionExtractor.harvest_rationale_comments`)
  which harvests these into low-confidence ``proposed`` decision records so they
  enter the queryable corpus with full retrieval/ranking/citation treatment, and
* the **query-time** MCP live-grep miner (``mcp_server/_code_rationale.py``),
  the best-effort recall backstop for un-indexed / just-edited files.

Keeping one implementation here means the marker list and the comment tokenizer
never drift between the two paths.

This module is deliberately dependency-free (stdlib only) so both the core
ingestion pipeline and the server layer can import it without a cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "RATIONALE_MARKERS",
    "CAUSAL_MARKERS",
    "LINE_PREFIXES",
    "C_BLOCK_EXTS",
    "CODE_EXTENSIONS",
    "CommentBlock",
    "HarvestedComment",
    "extract_comment_blocks",
    "has_rationale_marker",
    "has_causal_marker",
    "marker_strength",
    "is_license_or_boilerplate",
    "looks_like_commented_out_code",
    "harvest_file_rationale",
]

# ---------------------------------------------------------------------------
# Rationale markers
# ---------------------------------------------------------------------------
# A comment earns surfacing only if it reads like a *reason*, not a label.
# These are the causal / intent connectives and the well-known intent tags.
# Matched as lowercased substrings against the comment text. Curated to favor
# precision: a URL or a boilerplate header won't contain "because"/"instead".
RATIONALE_MARKERS: tuple[str, ...] = (
    "because",
    "instead of",
    "rather than",
    "in order to",
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

# The strong subset: markers that state an actual *reason*, an alternative
# rejected, or a deliberate choice — as opposed to a bare intent label
# (``note:``, ``always``, ``we use``). The index-time decision harvest requires
# one of these so a ``code_comment`` record always carries a genuine rationale;
# the MCP recall miner uses the full ``RATIONALE_MARKERS`` set instead.
CAUSAL_MARKERS: tuple[str, ...] = (
    "because",
    "instead of",
    "rather than",
    "in order to",
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
    "on purpose",
    "intentional",
    "intentionally",
    "deliberate",
    "deliberately",
    "for performance",
    "this is why",
    "do not remove",
    "don't remove",
)

# ---------------------------------------------------------------------------
# Comment syntax per file extension
# ---------------------------------------------------------------------------
# Line-comment prefixes keyed by extension. Kept pragmatic: covers the
# languages that make up the overwhelming majority of indexed source.
LINE_PREFIXES: dict[str, tuple[str, ...]] = {
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
C_BLOCK_EXTS: frozenset[str] = frozenset(
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

# The set of extensions this module can mine — anything we know a comment
# syntax for. Markdown / RST / plain text are deliberately excluded: their
# prose is full of causal words ("because") that are not code rationale, and
# README / docs already feed the readme_mining source.
CODE_EXTENSIONS: frozenset[str] = frozenset(LINE_PREFIXES) | C_BLOCK_EXTS

# ---------------------------------------------------------------------------
# Boilerplate / commented-out-code detection
# ---------------------------------------------------------------------------
# License headers, SPDX tags, copyright notices — the dominant false-positive
# class for a top-of-file comment block.
_LICENSE_RE = re.compile(
    r"""(
          copyright
        | \(c\)\s*\d{4}
        | spdx-license-identifier
        | all\ rights\ reserved
        | licensed\ under
        | redistribution\ and\ use
        | permission\ is\ hereby\ granted
        | gnu\ (general|lesser)\ public\ license
        | mit\ license
        | apache\ license
        | bsd\ license
        | this\ (file|software|program)\ is\ (free\ software|distributed)
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Encoding / shebang / editor-mode lines that lead a file.
_FILE_PREAMBLE_RE = re.compile(
    r"(-\*-\s*coding|coding[:=]\s*[-\w.]+|!/usr/bin|!/bin/|vim:|emacs:)",
    re.IGNORECASE,
)

# A line that "reads like code" rather than prose. Heuristic, not a parser:
# used only to drop commented-out code blocks that slipped past the marker gate.
_CODE_LINE_RE = re.compile(
    r"""(
          [;{}]\s*$                              # ends with ; { or }
        | ^\s*(def|class|return|import|from|if|elif|else|for|while|try
              |except|finally|with|raise|yield|async|await|public|private
              |protected|static|void|func|fn|impl|struct|enum|const|let
              |var|package|module|export|require)\b
        | =>                                     # arrow funcs / fat arrows
        | ^\s*@[\w.]+\s*\(?                       # decorators / annotations
        | ^\s*[\w.\[\]]+\s*=\s*\S                 # assignment
        | \w+\([^)]*\)\s*[:{;]?\s*$               # bare call / signature line
    )""",
    re.VERBOSE,
)

# A line carrying enough natural-language to count as prose: several word-ish
# tokens and no trailing code punctuation.
_WORD_RE = re.compile(r"[A-Za-z']{2,}")

# Bounds — keep a single harvested quote from flooding the corpus.
_MIN_COMMENT_CHARS = 25
_MIN_COMMENT_WORDS = 5
_MAX_COMMENT_CHARS = 600
_MAX_BLOCK_LINES = 12


@dataclass(frozen=True)
class CommentBlock:
    """A contiguous comment span. 1-indexed, inclusive line bounds."""

    start_line: int
    end_line: int
    text: str  # cleaned, single-spaced join of the comment body
    body_lines: tuple[str, ...] = field(default_factory=tuple)  # per-line bodies
    kind: str = "line"  # "line" | "block" (/* */) | "doc" (triple-quoted)


@dataclass(frozen=True)
class HarvestedComment:
    """A rationale comment that passed every precision guardrail."""

    start_line: int
    end_line: int
    text: str
    strength: int  # number of distinct rationale markers present


# ---------------------------------------------------------------------------
# Comment tokenizer
# ---------------------------------------------------------------------------


def _strip_comment_open(stripped: str, prefixes: tuple[str, ...]) -> str | None:
    """If ``stripped`` begins with a line-comment prefix, return the text after
    it; else None. Handles ``# x``, ``// x``, ``-- x`` and a bare ``#``."""
    for p in prefixes:
        if stripped.startswith(p):
            return stripped[len(p) :].lstrip()
    return None


# A line that is only a divider rule (``# -------`` / ``// =====`` / ``***``).
# These delimit comment runs and must never be glued into the rationale text.
_SEPARATOR_CHARS = frozenset("-=*~_#/+.<> ")


def _is_separator(body: str) -> bool:
    s = body.strip()
    return len(s) >= 3 and all(c in _SEPARATOR_CHARS for c in s)


def _trailing_comment(line: str, prefixes: tuple[str, ...]) -> str | None:
    """Recover a trailing inline comment (``x = 1  # because ...``).

    Conservative: requires whitespace before the marker so we don't trip on
    ``//`` inside a URL or ``#`` inside a string literal. Only the query-time
    MCP recall miner enables this (``include_trailing=True``); the index-time
    harvest leaves it off because trailing comments are almost always labels.
    """
    best: str | None = None
    best_idx = len(line) + 1
    for p in prefixes:
        idx = line.find(" " + p + " ")
        if idx == -1:
            idx = line.find("\t" + p)
        if idx != -1 and idx < best_idx:
            head = line[:idx].strip()
            if head:
                best_idx = idx
                best = line[idx:].lstrip()[len(p) :].lstrip()
    return best


def extract_comment_blocks(
    text: str, ext: str, *, include_trailing: bool = False
) -> list[CommentBlock]:
    """Return every comment block in ``text`` for a file of extension ``ext``.

    Consecutive full-line comments coalesce into one block (a multi-line
    rationale paragraph stays intact). Also recovers C-style ``/* */`` blocks
    and Python triple-quoted docstrings.

    Trailing inline comments (``x = 1  # because ...``) are captured only when
    ``include_trailing`` is set. At index time they are almost always labels,
    not rationale, so the decision harvest leaves it off (the default); the
    query-time MCP recall miner sets it ``True`` for maximum recall.
    Best-effort tokenizing, not a real parser.
    """
    ext = ext.lower()
    prefixes = LINE_PREFIXES.get(ext, ())
    lines = text.splitlines()
    blocks: list[CommentBlock] = []

    cur_start = 0
    cur_parts: list[str] = []

    def _flush() -> None:
        nonlocal cur_start, cur_parts
        if cur_parts:
            joined = " ".join(p for p in cur_parts if p).strip()
            if joined:
                blocks.append(
                    CommentBlock(
                        start_line=cur_start,
                        end_line=cur_start + len(cur_parts) - 1,
                        text=joined,
                        body_lines=tuple(cur_parts),
                        kind="line",
                    )
                )
        cur_start = 0
        cur_parts = []

    in_c_block = False
    in_pydoc = False
    pydoc_delim = ""
    block_start = 0
    block_parts: list[str] = []
    has_c_block = ext in C_BLOCK_EXTS
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
                    blocks.append(
                        CommentBlock(block_start, i, joined, tuple(block_parts), "block")
                    )
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
                    blocks.append(
                        CommentBlock(block_start, i, joined, tuple(block_parts), "doc")
                    )
                block_parts = []
            continue

        # --- full-line comment ---
        body = _strip_comment_open(stripped, prefixes) if prefixes else None
        if body is not None:
            # A divider rule ends the current run and is itself discarded, so a
            # section banner never glues two unrelated comments together.
            if _is_separator(body):
                _flush()
                continue
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
                    blocks.append(CommentBlock(i, i, seg, (seg,), "block"))
            else:
                in_c_block = True
                block_start = i
                block_parts = [stripped[open_idx + 2 :].lstrip("*").strip()]
            continue

        # --- start of a python docstring (module / class / function doc) ---
        if has_pydoc:
            m = re.match(r'^[rbRB]?("""|\'\'\')', stripped)
            if m:
                pydoc_delim = m.group(1)
                rest = stripped[m.end() :]
                close = rest.find(pydoc_delim)
                if close != -1:
                    seg = rest[:close].strip()
                    if seg:
                        blocks.append(CommentBlock(i, i, seg, (seg,), "doc"))
                else:
                    in_pydoc = True
                    block_start = i
                    block_parts = [rest.strip()]
                continue

        # --- trailing inline comment (recall miner only) ---
        if include_trailing and prefixes:
            tail = _trailing_comment(raw, prefixes)
            if tail:
                blocks.append(CommentBlock(i, i, tail, (tail,), "line"))

    _flush()
    return blocks


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def has_rationale_marker(text: str) -> bool:
    """True if ``text`` carries any rationale / intent marker."""
    low = text.lower()
    return any(m in low for m in RATIONALE_MARKERS)


def has_causal_marker(text: str) -> bool:
    """True if ``text`` states an actual reason / alternative / deliberate
    choice (the strong :data:`CAUSAL_MARKERS` subset)."""
    low = text.lower()
    return any(m in low for m in CAUSAL_MARKERS)


def marker_strength(text: str) -> int:
    """Count of distinct rationale markers present — used to rank a file's
    comments so the strongest survive the per-file cap."""
    low = text.lower()
    return sum(1 for m in RATIONALE_MARKERS if m in low)


def is_license_or_boilerplate(text: str) -> bool:
    """True for license headers, SPDX tags, copyright notices, shebang /
    encoding preambles — the dominant top-of-file false positives."""
    return bool(_LICENSE_RE.search(text) or _FILE_PREAMBLE_RE.search(text))


def _is_prose_line(s: str) -> bool:
    """A line that reads like a sentence fragment, not a statement of code."""
    if _CODE_LINE_RE.search(s):
        return False
    return len(_WORD_RE.findall(s)) >= 3


def looks_like_commented_out_code(body_lines: tuple[str, ...]) -> bool:
    """True when a comment block is disabled code, not a rationale paragraph.

    A genuine rationale carries at least one prose sentence; commented-out code
    is all statements. We drop a block that has no prose line, or whose code
    lines dominate prose by more than 2:1.
    """
    code = 0
    prose = 0
    for ln in body_lines:
        s = ln.strip()
        if not s:
            continue
        if _is_prose_line(s):
            prose += 1
        elif _CODE_LINE_RE.search(s):
            code += 1
    if code == 0:
        return False
    if prose == 0:
        return True
    return code > prose * 2


def _too_thin(text: str) -> bool:
    """A comment too short / sparse to be a real rationale."""
    if len(text) < _MIN_COMMENT_CHARS:
        return True
    return len(_WORD_RE.findall(text)) < _MIN_COMMENT_WORDS


# Leading comment cruft to strip from a surfaced quote (Sphinx ``#:`` markers,
# stray dividers, bullet glyphs) so the title/quote reads as prose.
_LEADING_JUNK_RE = re.compile(r"^[\s:#*/\-=.>•·]+")


def _clean(text: str) -> str:
    return _LEADING_JUNK_RE.sub("", text).strip()


def _truncate(text: str) -> str:
    if len(text) > _MAX_COMMENT_CHARS:
        return text[: _MAX_COMMENT_CHARS - 1].rstrip() + "…"
    return text


def harvest_file_rationale(
    text: str,
    ext: str,
    *,
    max_per_file: int = 3,
    include_docstrings: bool = False,
    require_causal: bool = True,
) -> list[HarvestedComment]:
    """Mine the rationale-bearing comments from one file's text.

    Applies every precision guardrail (rationale marker required, license /
    preamble headers dropped, commented-out code dropped, thin comments
    dropped) and returns at most ``max_per_file`` blocks, strongest first.

    ``ext`` must name a known code extension; non-code files return ``[]``.

    ``include_docstrings`` is ``False`` for the index-time decision harvest:
    triple-quoted docstrings are the documented API surface (already mined by
    the page generator / readme sources) and would flood the decision corpus.
    The hidden rationale this targets lives in ``#`` / ``//`` / ``/* */``
    comments. The MCP live-grep miner sets it ``True`` for maximum recall.

    ``require_causal`` (index-time default) demands a strong
    :data:`CAUSAL_MARKERS` reason rather than any intent label, so every
    ``code_comment`` decision carries a genuine rationale. The MCP recall miner
    sets it ``False`` to surface any marker-bearing comment.
    """
    ext = ext.lower()
    if ext not in CODE_EXTENSIONS:
        return []
    try:
        blocks = extract_comment_blocks(text, ext)
    except Exception:
        # A tokenizer edge case must never break ingestion.
        return []

    marker_test = has_causal_marker if require_causal else has_rationale_marker
    kept: list[HarvestedComment] = []
    for b in blocks:
        if b.kind == "doc" and not include_docstrings:
            continue
        body = _clean(b.text)
        if _too_thin(body):
            continue
        if not marker_test(body):
            continue
        # License / SPDX / shebang preamble is the dominant false positive.
        if is_license_or_boilerplate(body):
            continue
        if looks_like_commented_out_code(b.body_lines):
            continue

        end = b.end_line
        if end - b.start_line + 1 > _MAX_BLOCK_LINES:
            end = b.start_line + _MAX_BLOCK_LINES - 1
        kept.append(
            HarvestedComment(
                start_line=b.start_line,
                end_line=end,
                text=_truncate(body),
                strength=marker_strength(body),
            )
        )

    # Strongest markers first; stable on ties (earlier in file wins).
    kept.sort(key=lambda h: h.strength, reverse=True)
    return kept[:max_per_file]

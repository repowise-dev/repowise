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
    CODE_EXTENSIONS,
    RATIONALE_MARKERS,
    extract_comment_blocks,
    harvest_file_rationale,
)
from repowise.core.exclusion import build_exclude_spec, is_excluded
from repowise.server.mcp_server._why_relevance import (
    clears_floor,
    floor_ranked,
    question_terms,
    relevance,
    term_idf,
    word_set,
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
# Keep a block only if it scores at least this fraction of the best block's
# score. Relative, not absolute: scores scale with how many terms a question has.
_RELEVANCE_FLOOR_FRACTION = 0.5
_NEAR_LINE_WINDOW = 60
# Added to a block's weighted coverage (0 to 1). A marker says the block is a
# reason and ranks it over an equal-coverage description; a near line is the
# symbol get_answer anchored.
_MARKER_BONUS = 0.25
_NEAR_LINE_BONUS = 0.25


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
# the digit. So it is matched separately, as a whole token, and weighed by rarity
# like any term: "40" on one comment decides, "3" on every "Tier 3" does not.
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


def _number_term(num: str) -> str:
    """A number's key among the question terms; no word can collide with it."""
    return f"#{num}"


def _score_block(
    words: frozenset[str], own: frozenset[str], low: str, idf: dict[str, float]
) -> tuple[float, list[str], bool] | None:
    """Score a comment block, or ``None`` when it is not served.

    Returns (score, matched_terms, has_marker). The score is the question's
    weighted coverage (rarer terms weigh more, over every block scanned) plus
    the bonuses above. Uses the broad ``RATIONALE_MARKERS`` set shared with the
    index-time harvest, in recall mode, so intent-only markers ("never",
    "always") count.

    The gate: with question terms, a marked block needs one of their words in
    its own text (*own*; *words* adds what an anchoring declaration lends) and
    an unmarked block also needs the floor's share of their weight; a number
    alone never qualifies, so "Strategy 3" does not answer "tier 3". Without
    terms (path mode, "why is this file shaped this way") a marker is enough.
    """
    has_marker = any(m in low for m in RATIONALE_MARKERS)
    if not idf:
        return (_MARKER_BONUS, [], True) if has_marker else None
    matched = [t for t in idf if t in words]
    coverage = relevance(words, idf)
    if not any(not t.startswith("#") and t in own for t in matched):
        return None
    if not (has_marker or clears_floor(coverage)):
        return None
    score = coverage + (_MARKER_BONUS if has_marker else 0.0)
    return score, sorted(t.lstrip("#") for t in matched), has_marker


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _query_identifiers(query: str | None) -> set[str]:
    """The code names in *query*, lowercased: tokens that split into parts
    (``_narrow_overload``, ``withManagedSignal``) or lead with an underscore."""
    return {
        raw.lower()
        for raw in _IDENT_RE.findall(query or "")
        if raw.startswith("_") or len(word_set(raw)) > 1
    }


# A line that declares a name: a keyword declaration, or ``NAME =`` /
# ``NAME:`` at its start. A call site naming the identifier is not one.
_DECL_KEYWORD_RE = re.compile(
    r"\s*(?:(?:export|public|private|protected|internal|static|async|final|pub|"
    r"readonly|abstract|default|override|open|inline)\s+)*"
    r"(?:def|class|fn|func|function|const|let|var|val|type|interface|struct|enum)\b"
)
_DECL_ASSIGN_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::|=(?!=))")


def _declares(line: str, idents: set[str]) -> bool:
    """Whether *line* declares one of *idents*."""
    m = _DECL_ASSIGN_RE.match(line)
    if m and m.group(1).lower() in idents:
        return True
    return bool(_DECL_KEYWORD_RE.match(line)) and bool(
        idents & {w.lower() for w in _IDENT_RE.findall(line)}
    )


def _anchored_words(lines: list[str], start: int, end: int, idents: set[str]) -> frozenset[str]:
    """Words of the declaration a block documents, when it declares a query identifier.

    A docstring sits under its ``def``, a comment above its declaration, and
    neither repeats the name, so the question's strongest term never occurs in
    the block itself. The line before *start* or the first non-blank line after
    *end* (1-based) declaring one of *idents* lends the block its words.
    """
    if not idents:
        return frozenset()
    near = [lines[start - 2]] if start >= 2 else []
    near += [ln for ln in lines[end : end + 3] if ln.strip()][:1]
    out: frozenset[str] = frozenset()
    for ln in near:
        if _declares(ln, idents):
            out |= word_set(ln)
    return out


# A mined row may stand in for the answer only on this many distinct matches
# (question terms or numbers). A marker plus one shared word clears `_keep`
# ("FIXME ... unused by cobra" for a question naming cobra) but answers nothing.
_LEAD_MIN_MATCHES = 2


def can_lead_answer(row: dict) -> bool:
    """Whether a mined rationale row matches enough of the question to be the answer."""
    return len(set(row.get("matched_terms") or ())) >= _LEAD_MIN_MATCHES


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


def _file_blocks(
    root: Path, path: str, idents: set[str], numbers: list[str]
) -> list[tuple[str, int, int, str, frozenset[str], frozenset[str]]]:
    """``(path, start, end, comment, words, own words)`` per comment block of *path*."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    text = _read_text(root, path)
    if text is None or text.count("\n") > _MAX_FILE_LINES:
        return []
    try:
        found = extract_comment_blocks(text, ext, include_trailing=True)
    except Exception as exc:  # never let a tokenizer bug break a tool
        _log.debug("comment extraction failed for %s: %s", path, exc)
        return []
    lines = text.splitlines()
    out = []
    for block in found:
        start, end, comment = block.start_line, block.end_line, block.text
        low = comment.lower()
        own = word_set(comment) | {_number_term(n) for n in numbers if _number_in_text(n, low)}
        lent = _anchored_words(lines, start, end, idents)
        out.append((path, start, end, comment, own | lent, own))
    return out


def mine_rationale(
    repo_root: Any,
    file_paths: list[str],
    query: str | None,
    *,
    near_lines: dict[str, int] | None = None,
    max_files: int = _MAX_FILES,
    max_results: int = _MAX_RESULTS,
    truncate_blocks: bool = True,
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

    terms = question_terms(query, prose=True) if query else []
    numbers = _salient_numbers(query) if terms else []
    idents = _query_identifiers(query)
    near_lines = near_lines or {}

    # Dedupe while preserving order; cap the file fan-out.
    ordered = list(dict.fromkeys(p for p in file_paths if p))[:max_files]
    # Every block of every scanned file first: term rarity is taken over all of
    # them, so a word most comments here share weighs little.
    blocks = [b for path in ordered for b in _file_blocks(root, path, idents, numbers)]
    terms += [_number_term(n) for n in numbers]
    idf = term_idf(terms, [b[4] for b in blocks]) if terms else {}

    scored: list[tuple[float, dict]] = []
    for path, start, end, comment, words, own in blocks:
        result = _score_block(words, own, comment.lower(), idf)
        if result is None:
            continue
        score, matched, _ = result
        near = near_lines.get(path)
        if near is not None and abs(start - near) <= _NEAR_LINE_WINDOW:
            score += _NEAR_LINE_BONUS
        # Coalesced runs longer than the cap are split at the head: the
        # lead lines carry the rationale; tail is usually elaboration.
        if end - start + 1 > _MAX_BLOCK_LINES:
            end = start + _MAX_BLOCK_LINES - 1
        scored.append(
            (
                score,
                {
                    "path": path,
                    "lines": [start, end],
                    "comment": _truncate_block(comment) if truncate_blocks else comment,
                    "matched_terms": matched,
                },
            )
        )

    scored.sort(key=lambda t: t[0], reverse=True)

    # Relevance floor, relative to the best block: below half the top score a
    # block cleared the gate on generic vocabulary rather than on the
    # question — an SVG edge-routing docstring on "edges"/"project"/"between".
    if scored:
        floor = scored[0][0] * _RELEVANCE_FLOOR_FRACTION
        scored = [t for t in scored if t[0] >= floor]
    return [entry for _, entry in scored[:max_results]]


# Cap / limit vocabulary: a question may say "capped" while the source comment
# says "limit" (or vice versa). Treat the family as interchangeable anchors so a
# number-free "why is X limited" question still has something to grep on.
_CAP_FAMILY: tuple[str, ...] = ("cap", "limit", "max", "bound", "ceiling", "threshold")

# Git-grep is bounded so a pathological pattern can't stall a tool response.
_GREP_TIMEOUT_S = 20
# A pasted issue body carries 100+ nouns and numbers; an uncapped alternation
# matches most comment lines in the repo. Longest nouns are the most specific:
# on sample issue bodies they ranked the target no worse than the uncapped or
# first-12 sets, and a minimum per-file count added nothing.
_MAX_GREP_NOUNS = 12
_MAX_GREP_NUMBERS = 4


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
    numbers = _salient_numbers(query)[:_MAX_GREP_NUMBERS]
    terms = _content_terms(query)
    nouns = sorted((t for t in terms if t not in _CAP_FAMILY), key=lambda t: (-len(t), t))
    nouns = nouns[:_MAX_GREP_NOUNS]
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
                # -c: one ``path:count`` line per file, so the output is bounded
                # by the file count, not by how many lines a broad pattern hits.
                "-c",
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
            # Matched source lines come back verbatim, so this is the most
            # encoding-sensitive git output we read. text=True alone decodes
            # with the locale codec; one non-cp1252 byte in any hunk raised
            # UnicodeDecodeError, which the handler below swallows, so get_why
            # returned no rationale anchors at all rather than fewer.
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=_GREP_TIMEOUT_S,
        )
    except Exception:
        return []
    counts: Counter[str] = Counter()
    for line in proc.stdout.splitlines():
        path, _, n = line.rpartition(":")
        if path and n.isdigit():
            counts[path] += int(n)
    # ``git grep`` only scans tracked files, but a gitignored copy can still be
    # tracked (or land here via a future --no-index retry); filter the winners
    # through the repo's exclusion rules so an ignored path is never anchored on.
    spec = build_exclude_spec(root)
    ranked = [p for p, _ in counts.most_common() if not is_excluded(p, spec)]
    return ranked[:max_files]


# One repository's harvest, keyed on (root, HEAD): harvesting reads every
# tracked code file (about 2s cold on a 3,000-file repository), so a session
# asking several questions pays it once. Ceiling: edits since HEAD are not seen
# until the next commit; the path-scoped miner above stays live for those.
_HARVEST_CACHE: dict[tuple[str, str], list[dict]] = {}
_MAX_HARVEST_FILES = 20_000


def _head(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "--no-pager", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_GREP_TIMEOUT_S,
        )
    except Exception:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _harvest_repo(root: Path) -> list[dict]:
    """Every tracked file's strong-marker comments, strongest first per file.

    ``harvest_file_rationale`` with its defaults: a causal marker is required
    and docstrings are skipped. That is the precision bar a comment must clear
    to be served for a question naming no file, where nothing else vouches for
    it. These are never decisions (#751); they are served as comments.
    """
    try:
        proc = subprocess.run(
            ["git", "--no-pager", "ls-files", "-z"],
            cwd=str(root),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=_GREP_TIMEOUT_S,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    spec = build_exclude_spec(root)
    rows: list[dict] = []
    paths = [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]
    for path in paths[:_MAX_HARVEST_FILES]:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in CODE_EXTENSIONS or is_excluded(path, spec):
            continue
        text = _read_text(root, path)
        if text is None:
            continue
        for h in harvest_file_rationale(text, ext):
            rows.append({"path": path, "lines": [h.start_line, h.end_line], "comment": h.text})
    return rows


def repo_rationale(repo_root: Any, query: str, *, max_results: int = 3) -> list[dict]:
    """Strong-marker comments anywhere in the repository that answer *query*.

    For a question naming no file, when no decision record matched. Ranked by
    the decision store's own relevance floor, with rarity taken over the whole
    harvest, so a comment is served only when it carries most of the
    question's weighted vocabulary. Rows have the ``mine_rationale`` shape.
    Best-effort: ``[]`` on any failure.
    """
    if not repo_root or not query:
        return []
    try:
        root = Path(str(repo_root))
        head = _head(root)
        if head is None:
            return []
        key = (str(root.resolve()), head)
        if key not in _HARVEST_CACHE:
            _HARVEST_CACHE.clear()
            _HARVEST_CACHE[key] = _harvest_repo(root)
        return floor_ranked(query, _HARVEST_CACHE[key], lambda r: r["comment"], max_results)
    except Exception as exc:  # never let the harvest break a tool
        _log.debug("repo rationale harvest failed: %s", exc)
        return []

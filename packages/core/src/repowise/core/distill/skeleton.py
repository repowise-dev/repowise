"""Index-backed file skeletonization — slice on persisted symbol bounds.

A skeleton is the file with symbol *bodies* elided: imports and module
docstring kept, every signature kept, bodies replaced by one-line elision
markers carrying the omitted line range. Because ``WikiSymbol`` rows persist
``start_line``/``end_line`` at index time, the skeleton is produced by pure
line slicing — **no parser runs at query time** (~1 ms for a large file).

Two modes:

- ``"signatures"`` — structure only. Every signature present, all bodies
  elided. The cheapest faithful map of a file. Docstrings are NOT kept here
  (they push a typical Python file from ~15% to ~20% of full tokens, and the
  index already serves them via get_context symbol cards).
- ``"smart"`` — signatures plus docstring summary lines plus the bodies of
  the most *important* symbols,
  ranked by caller-provided importance (PageRank), query-name match, with a
  per-symbol line budget proportional to importance, all under a total token
  budget. A hotspot file gets a larger budget — high-churn code is where
  body-level context pays off.

This module is pure: it sees source text and symbol records, never a
database. Callers (MCP tools, hooks, tests) fetch the rows and pass them in.
Degradation is graceful by construction — no usable symbols means the source
is returned untouched (``mode="raw"``), never an error.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from repowise.core.distill.budget import estimate_tokens

__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "SkeletonResult",
    "SkeletonSymbol",
    "build_skeleton",
    "estimate_skeleton_tokens",
]

#: Default total token budget for smart mode (signatures + kept bodies).
DEFAULT_TOKEN_BUDGET = 1800

#: Hotspot files get proportionally more body context.
_HOTSPOT_BUDGET_FACTOR = 1.25

#: Max lines scanned past a symbol's start to find the end of its signature.
_SIG_SCAN_MAX = 12

#: Docstring lines kept directly under a signature before eliding the rest.
#: One line = the summary; the elision marker right after it signals more.
_DOCSTRING_KEEP_LINES = 1

#: Module-level gaps (constants, registrations) up to this size are kept
#: verbatim — an elision marker would not be shorter.
_GAP_KEEP_LINES = 4

#: Preambles up to this size are kept whole; longer ones are filtered down
#: to the leading docstring/comment block plus import-shaped lines.
_PREAMBLE_KEEP_LINES = 60

#: Smart mode keeps at most this many symbol bodies.
_SMART_MAX_BODIES = 5

#: A kept body always gets at least this many lines (below that, the head
#: of a function is rarely worth more than its signature).
_SMART_MIN_BODY_LINES = 4

_IMPORT_RE = re.compile(
    r"^\s*(import\s|from\s+\S+\s+import\s|use\s|require\s*\(|#include\s|using\s|package\s)"
)

#: Decorative comment rules (``# ----``, ``// ====``) carry zero information;
#: they are dropped from the skeleton without a marker.
_DECOR_RE = re.compile(r"^\s*(?:#|//)\s*[-=~*#_]{4,}\s*$")

_DOCSTRING_DELIMS = ('"""', "'''")


@dataclass(frozen=True)
class SkeletonSymbol:
    """One indexed symbol, as persisted on a ``WikiSymbol`` row.

    ``importance`` is whatever ranking signal the caller has — symbol-node
    PageRank in the indexed case, 0.0 when the graph has nothing. The
    skeleton only compares importances relative to each other.
    """

    name: str
    kind: str
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    signature: str = ""
    importance: float = 0.0


@dataclass(frozen=True)
class SkeletonResult:
    """Outcome of one skeletonization."""

    text: str
    mode: str  # "smart" | "signatures" | "raw"
    full_tokens: int
    skeleton_tokens: int
    symbol_count: int
    bodies_kept: tuple[str, ...] = field(default=())

    @property
    def pct_of_full(self) -> float:
        if self.full_tokens <= 0:
            return 100.0
        return self.skeleton_tokens / self.full_tokens * 100.0


def build_skeleton(
    source: str,
    symbols: Sequence[SkeletonSymbol],
    *,
    mode: str = "smart",
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    query: str | None = None,
    hotspot: bool = False,
) -> SkeletonResult:
    """Skeletonize *source* by slicing on the persisted bounds in *symbols*.

    Pure line arithmetic — no parsing. When *symbols* is empty or none of the
    bounds are usable, the source is returned untouched with ``mode="raw"``
    (the caller's signal that the index could not help here).
    """
    full_tokens = estimate_tokens(source)
    lines = source.splitlines()
    total = len(lines)

    usable = _sanitize(symbols, total)
    if not usable:
        return SkeletonResult(
            text=source,
            mode="raw",
            full_tokens=full_tokens,
            skeleton_tokens=full_tokens,
            symbol_count=0,
        )

    keep = [False] * total

    # Preamble: module docstring + imports + leading module code.
    first_start = usable[0].start_line - 1  # 0-indexed
    _keep_preamble(lines, first_start, keep)

    # Signatures; smart mode also keeps the docstring summary line under each.
    smart = mode == "smart"
    sig_ends: dict[int, int] = {}  # symbol index -> 0-indexed signature end
    for idx, sym in enumerate(usable):
        s = sym.start_line - 1
        e = _signature_end(lines, s, sym.end_line - 1)
        sig_ends[idx] = e
        for i in range(s, e + 1):
            keep[i] = True
        if smart:
            for i in _docstring_lines(lines, e + 1, sym.end_line - 1):
                keep[i] = True

    # Small module-level gaps between symbols stay verbatim.
    _keep_small_gaps(lines, usable, total, keep)

    bodies_kept: tuple[str, ...] = ()
    if smart:
        budget = int(token_budget * (_HOTSPOT_BUDGET_FACTOR if hotspot else 1.0))
        bodies_kept = _keep_smart_bodies(lines, usable, sig_ends, keep, budget, query)

    # Decorative banner comments add bytes, not structure.
    for i in range(total):
        if keep[i] and _DECOR_RE.match(lines[i]):
            keep[i] = False

    text = _render(lines, keep)
    return SkeletonResult(
        text=text,
        mode=mode if mode in ("smart", "signatures") else "signatures",
        full_tokens=full_tokens,
        skeleton_tokens=estimate_tokens(text),
        symbol_count=len(usable),
        bodies_kept=bodies_kept,
    )


def estimate_skeleton_tokens(
    symbol_bounds: Sequence[tuple[int, int]],
    *,
    file_size_bytes: int,
    total_lines: int | None = None,
) -> int:
    """Cheap skeleton-size estimate from bounds arithmetic alone.

    For hook paths that must not render anything: approximates the kept
    fraction (preamble + ~2 signature lines + 1 elision line per symbol)
    and scales the file's chars/4 token count by it. Within ~2x of the real
    skeleton size, which is all a "~M tokens vs K" nudge needs.
    """
    if not symbol_bounds or file_size_bytes <= 0:
        return max(0, file_size_bytes // 4)
    starts = [s for s, _ in symbol_bounds if s > 0]
    if not starts:
        return file_size_bytes // 4
    lines_total = total_lines or max(e for _, e in symbol_bounds)
    if lines_total <= 0:
        return file_size_bytes // 4
    preamble = min(min(starts) - 1, _PREAMBLE_KEEP_LINES)
    kept_lines = preamble + 3 * len(symbol_bounds)  # ~2 sig lines + 1 marker
    fraction = min(1.0, kept_lines / lines_total)
    return max(1, int(file_size_bytes // 4 * fraction))


# ---------------------------------------------------------------------------
# Internals — all 0-indexed line arithmetic from here down
# ---------------------------------------------------------------------------


def _sanitize(symbols: Sequence[SkeletonSymbol], total: int) -> list[SkeletonSymbol]:
    """Drop synthetic/out-of-range rows; clamp and sort by start line."""
    out: list[SkeletonSymbol] = []
    for sym in symbols:
        if sym.name == "__module__" or sym.kind == "module":
            continue  # synthetic module-level node, spans nothing real
        if sym.start_line < 1 or sym.start_line > total:
            continue
        end = max(sym.start_line, min(sym.end_line, total))
        if end != sym.end_line:
            sym = SkeletonSymbol(
                name=sym.name,
                kind=sym.kind,
                start_line=sym.start_line,
                end_line=end,
                signature=sym.signature,
                importance=sym.importance,
            )
        out.append(sym)
    out.sort(key=lambda s: (s.start_line, -(s.end_line - s.start_line)))
    return out


def _keep_preamble(lines: list[str], first_start: int, keep: list[bool]) -> None:
    """Mark the pre-symbol region: whole if small, docstring+imports if not."""
    if first_start <= 0:
        return
    if first_start <= _PREAMBLE_KEEP_LINES:
        for i in range(first_start):
            keep[i] = True
        return
    # Long preamble: leading docstring/comment block, then import-shaped lines.
    i = 0
    in_docstring = False
    while i < first_start:
        stripped = lines[i].strip()
        if i == 0 or in_docstring or stripped.startswith("#") or (not stripped and i < 3):
            keep[i] = True
            opens = sum(stripped.count(d) for d in _DOCSTRING_DELIMS)
            if not in_docstring and any(stripped.startswith(d) for d in _DOCSTRING_DELIMS):
                in_docstring = opens == 1
            elif in_docstring and opens:
                in_docstring = False
            i += 1
            continue
        break
    for j in range(i, first_start):
        if _IMPORT_RE.match(lines[j]):
            keep[j] = True


def _signature_end(lines: list[str], start: int, end: int) -> int:
    """Last 0-indexed line of the signature starting at *start*.

    Bracket-balance scan, language-agnostic: the signature ends on the first
    line where parens/brackets are balanced and the line closes with a body
    opener (``:``/``{``), a terminator (``;``), or the param list itself.
    Allman-style braces (``{`` alone on the next line) are folded in. Falls
    back to the start line when nothing matches within the scan window.
    """
    depth = 0
    last = min(start + _SIG_SCAN_MAX - 1, end)
    for i in range(start, last + 1):
        line = lines[i]
        depth += line.count("(") - line.count(")")
        depth += line.count("[") - line.count("]")
        if depth > 0:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith((":", "{", ";", "=>")):
            return i
        # Signature closed without a body opener — check for an Allman brace.
        j = i + 1
        if j <= end and lines[j].strip().startswith("{"):
            return j
        return i
    return start


def _docstring_lines(lines: list[str], start: int, end: int) -> list[int]:
    """Indices of a docstring block directly under a signature, capped.

    Only triggers when the first non-blank line after the signature opens a
    triple-quoted string — pure Python convention, harmless elsewhere. Keeps
    at most ``_DOCSTRING_KEEP_LINES`` lines; the closing delimiter line is
    always included when within the cap so the skeleton stays well-formed.
    """
    i = start
    while i <= end and not lines[i].strip():
        i += 1
    if i > end:
        return []
    stripped = lines[i].strip()
    delim = next((d for d in _DOCSTRING_DELIMS if stripped.startswith(d)), None)
    if delim is None:
        return []
    # Single-line docstring: opener and closer on the same line.
    if stripped.count(delim) >= 2:
        return [i]
    kept = [i]
    j = i + 1
    while j <= end and len(kept) < _DOCSTRING_KEEP_LINES:
        kept.append(j)
        if delim in lines[j]:
            return kept
        j += 1
    return kept


def _keep_small_gaps(
    lines: list[str],
    symbols: list[SkeletonSymbol],
    total: int,
    keep: list[bool],
) -> None:
    """Keep short module-level runs between symbols (constants, registrations)."""
    # Gap candidates: between each symbol end and the next symbol start; the
    # last symbol's "next start" is EOF, which covers the trailing region.
    spans: list[tuple[int, int]] = []
    starts = sorted(s.start_line - 1 for s in symbols)
    ends = sorted(s.end_line for s in symbols)  # 0-indexed exclusive
    for e in ends:
        nxt = next((st for st in starts if st >= e), total)
        if e < nxt:
            spans.append((e, nxt))
    for a, b in spans:
        unkept = [i for i in range(a, min(b, total)) if not keep[i]]
        if 0 < len(unkept) <= _GAP_KEEP_LINES:
            for i in unkept:
                keep[i] = True


def _query_bonus(name: str, query: str | None) -> float:
    if not query:
        return 0.0
    name_l = name.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) >= 3]
    return 0.75 if any(t in name_l for t in tokens) else 0.0


def _keep_smart_bodies(
    lines: list[str],
    symbols: list[SkeletonSymbol],
    sig_ends: dict[int, int],
    keep: list[bool],
    token_budget: int,
    query: str | None,
) -> tuple[str, ...]:
    """Mark the bodies of the top-ranked leaf symbols, budget-proportionally."""
    starts = sorted(s.start_line - 1 for s in symbols)

    # Leaf symbols only: a class body is its methods' signatures, which the
    # skeleton already shows — keeping it whole would re-inline everything.
    candidates: list[tuple[float, int]] = []  # (score, symbol index)
    max_importance = max((s.importance for s in symbols), default=0.0)
    for idx, sym in enumerate(symbols):
        body_start = sig_ends[idx] + 1
        body_end = sym.end_line - 1
        if body_end - body_start + 1 <= _GAP_KEEP_LINES:
            continue  # tiny body — render rule keeps short runs anyway
        if any(body_start <= st <= body_end for st in starts):
            continue  # contains another symbol — not a leaf
        norm = sym.importance / max_importance if max_importance > 0 else 0.0
        score = norm + _query_bonus(sym.name, query)
        if score <= 0.0:
            continue
        candidates.append((score, idx))

    if not candidates:
        return ()

    candidates.sort(key=lambda t: (-t[0], symbols[t[1]].start_line))
    top = candidates[:_SMART_MAX_BODIES]

    base_tokens = estimate_tokens(_render(lines, keep))
    body_budget = max(0, token_budget - base_tokens)
    if body_budget <= 0:
        return ()
    avg_line_tokens = max(1.0, estimate_tokens("\n".join(lines)) / max(1, len(lines)))
    budget_lines = int(body_budget / avg_line_tokens)
    if budget_lines < _SMART_MIN_BODY_LINES:
        return ()

    score_sum = sum(score for score, _ in top) or 1.0
    kept_names: list[str] = []
    remaining = budget_lines
    for score, idx in top:
        if remaining < _SMART_MIN_BODY_LINES:
            break
        sym = symbols[idx]
        body_start = sig_ends[idx] + 1
        body_end = sym.end_line - 1
        body_len = body_end - body_start + 1
        # Per-symbol line budget proportional to importance share.
        share = max(_SMART_MIN_BODY_LINES, int(budget_lines * score / score_sum))
        take = min(body_len, share, remaining)
        if take < _SMART_MIN_BODY_LINES:
            continue
        for i in range(body_start, body_start + take):
            keep[i] = True
        remaining -= take
        kept_names.append(sym.name)
    return tuple(kept_names)


def _render(lines: list[str], keep: list[bool]) -> str:
    """Emit kept lines verbatim; collapse omitted runs to one marker line.

    Runs of one or two omitted lines are kept verbatim — a marker would not
    be shorter — unless they are pure blank/decoration, which is dropped
    silently. The marker carries the 1-indexed range so an agent can jump
    straight back with a ranged Read.
    """
    out: list[str] = []
    i = 0
    total = len(lines)
    while i < total:
        if keep[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < total and not keep[j]:
            j += 1
        if j - i <= 2:
            run = lines[i:j]
            if not all(not ln.strip() or _DECOR_RE.match(ln) for ln in run):
                out.extend(run)
        else:
            indent = re.match(r"[ \t]*", lines[i]).group(0)
            out.append(f"{indent}... {j - i} lines ({i + 1}-{j})")
        i = j
    return "\n".join(out) + ("\n" if out else "")

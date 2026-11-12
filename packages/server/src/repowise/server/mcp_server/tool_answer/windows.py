"""Source windows: the live code a ``get_answer`` reply serves in place of pointers.

The files the reply ranks for the question are read from disk and served in
Read's own shape (``<line>\\t<text>``), a window per anchored definition,
extended from the definition line to the end of the symbol and capped. Windows
in one file that sit close together merge; a longer gap is a marker line naming
the definitions it skips. The top file is served first, then one window per
further file, then the rest while the byte budget lasts.

``delivered`` is computed from what was rendered, never asserted: which lines
of which file were sent, which definitions were cut and where, which ranked
files were not shown, and the exact names to pass back to ``get_answer`` for
more. A question made only of such names is a continuation: it gets their
windows straight from the index, with no retrieval and no synthesis.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.server.mcp_server._helpers import read_repo_file_text

_log = logging.getLogger("repowise.mcp.answer")

# A window runs from the definition line to the end of its symbol, at most
# this many lines: agents read forward from a definition, a median of about 60
# lines past it. Long docstring and comment runs are shortened inside it, so
# the span is wider than the code lines it shows.
WINDOW_MAX_LINES = 120
# Context before an anchor that is not a definition line (a cited line inside a
# long body): the median agent read starts about 11 lines above it.
_LEAD_LINES = 11
# Two windows in one file closer than this merge into one.
_MERGE_GAP = 8
# Files a reply windows, and the share of the budget the top file may take.
MAX_FILES = 4
_OTHER_FILE_SHARE = 0.3
# A further file's first window: enough to show what it holds (about 25 lines).
_OTHER_FIRST_BYTES = 900
# Definitions a file contributes for carrying the question's words.
_WORD_ANCHORS = 6
# Share of the budget the named definitions may take before other files get theirs.
_NAMED_SHARE = 0.6
# Anchor ranks that name a definition: bodies, cited lines, exact question names.
_NAMED_RANK = 1
# A named definition gets at least this many bytes (about 30 lines) when it is sent.
_NAMED_MIN_BYTES = 1_400
# Below this many lines a window that does not fit is dropped rather than cut.
_MIN_CUT_LINES = 12
# A lower-ranked file is windowed only when its retrieval score reaches this
# share of the top file's: the floor scales with the best file.
_RELEVANCE_FLOOR = 0.45
# Bytes of serialized source a reply may spend, and the floor it always gets.
SOURCE_BUDGET_BYTES = 8_600
SOURCE_MIN_BYTES = 2_500
# Docstring and comment runs longer than this keep their first lines only.
_PROSE_RUN = 4
_PROSE_KEEP = 2
_PROSE_MARK = "not shown (docstring or comment)"
# Bytes reserved per gap marker line when budgeting a window.
_MARKER_BYTES = 90
# Names a gap marker and the next-inputs list may carry.
_GAP_NAMES = 4
_NEXT_NAMES = 5

_CODE_KINDS = frozenset({"function", "method", "class", "interface", "struct", "enum",
                         "trait", "type", "type_alias", "module", "impl", "record"})
_CONTAINER_KINDS = frozenset({"class", "interface", "struct", "trait", "impl", "record",
                              "enum", "module"})
_REF_RE = re.compile(r"`?([\w./\\-]+\.\w+):(\d+)(?:-(\d+))?")
_WORD_RE = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])")
_STOPWORDS = frozenset({
    "how", "does", "what", "where", "when", "which", "why", "the", "and", "that", "this",
    "from", "with", "into", "for", "are", "its", "work", "works", "code", "file",
    "files", "make", "made", "used", "uses", "using", "decide", "decides", "should", "would",
    "could", "whether", "there", "their", "they", "them", "gets", "sets", "like", "such",
    "than", "then", "also", "only", "each", "every", "other", "some",
})


@dataclass
class _Sym:
    name: str
    qualified: str
    kind: str
    start: int
    end: int
    parent: str | None


@dataclass
class _Window:
    start: int
    end: int
    rank: int  # anchor priority: lower is stronger
    names: list[str] = field(default_factory=list)


@dataclass
class _FilePlan:
    path: str
    lines: list[str]
    symbols: list[_Sym]
    windows: list[_Window]
    whole: bool = False
    prose: list[bool] | None = None


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _row_path(row: Any) -> str | None:
    if isinstance(row, str):
        return _norm(row)
    if isinstance(row, dict):
        p = row.get("path") or row.get("file") or row.get("target_path")
        return _norm(p.split("::", 1)[0]) if isinstance(p, str) else None
    return None


def _paths(raw: dict[str, Any], *keys: str) -> list[str]:
    """The file of every row under *keys*, in order, once each."""
    return list(dict.fromkeys(p for key in keys for p in map(_row_path, raw.get(key) or []) if p))


def _scores(raw: dict[str, Any]) -> dict[str, float]:
    """The retrieval score of each ranked file, first row wins."""
    scores: dict[str, float] = {}
    rows = [*(raw.get("retrieval") or []), *(raw.get("best_guesses") or [])]
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("score"), int | float):
            scores.setdefault(_row_path(row) or "", float(row["score"]))
    return scores


def ranked_files(raw: dict[str, Any]) -> tuple[list[str], list[str], dict[str, float]]:
    """``(leading, pool, scores)``: the files a reply must lead with, the ranked rest.

    A synthesized answer leads with the files it cites, a keyless reply with
    the bodies of the names it was asked about. The pool is every other file
    the reply ranks: retrieval, the guesses, then the candidate files.
    """
    lead_keys = ("symbol_bodies",) if raw.get("degraded") else ("citations", "symbol_bodies")
    leading = _paths(raw, *lead_keys)
    pool = [p for p in _paths(raw, "retrieval", "best_guesses", "candidate_files") if p not in leading]
    return leading, pool, _scores(raw)


def question_stems(question: str) -> set[str]:
    """Content words of the question, cut to a five-letter stem."""
    return {
        w.lower()[:5] for w in _WORD_RE.findall(question)
        if len(w) > 3 and w.lower() not in _STOPWORDS
    }


def _rerank(pool: list[str], question: str, symbols: dict[str, list[_Sym]]) -> list[tuple[str, int]]:
    """``(path, hits)`` with the pool re-ordered by question words its names carry.

    A file's path and the names it defines are matched against the question's
    content words; each word found moves the file up two places in the
    retrieval order, which breaks ties. Keyless retrieval ranks the right file
    second or third often enough that its definitions are the better signal.
    """
    words = question_stems(question)
    scored = []
    for i, path in enumerate(pool):
        names = " ".join(s.name for s in symbols.get(path, ()))
        hits = len(words & question_stems(f"{path} {names}"))
        scored.append((i * 0.5 - hits, i, path, hits))
    return [(path, hits) for *_, path, hits in sorted(scored)]


def _admit(
    leading: list[str], ranked: list[tuple[str, int]], scores: dict[str, float]
) -> list[str]:
    """The files worth windows: the leading ones, then ranked files above the floor.

    The floor scales with the best file: a ranked file is windowed when its
    retrieval score reaches a share of the top score, or when its names carry
    a question word.
    """
    top = max(scores.values(), default=0.0)
    admitted = list(leading[:MAX_FILES])
    for path, hits in ranked:
        if len(admitted) >= MAX_FILES:
            break
        score = scores.get(path)
        if not admitted or hits or (top and score is not None and score >= _RELEVANCE_FLOOR * top):
            admitted.append(path)
    return admitted


_Anchor = tuple[int, int | None, int]  # (line, end or None, rank: lower is stronger)


def _span(row: Any) -> tuple[int, int] | None:
    lines = row.get("lines") if isinstance(row, dict) else None
    if isinstance(lines, list | tuple) and len(lines) == 2 and all(isinstance(v, int) for v in lines):
        return lines[0], lines[1]
    return None


def _rows_in(raw: dict[str, Any], key: str, path: str) -> list[dict]:
    return [r for r in raw.get(key) or [] if isinstance(r, dict) and _row_path(r) == path]


def _answer_refs(raw: dict[str, Any], path: str) -> list[_Anchor]:
    """``path:line`` and ``path:a-b`` citations the answer prose writes for *path*."""
    answer = raw.get("answer") if isinstance(raw.get("answer"), str) else ""
    out: list[_Anchor] = []
    for m in _REF_RE.finditer(answer):
        ref = _norm(m.group(1))
        if path == ref or path.endswith("/" + ref.removeprefix("./")):
            start, end = int(m.group(2)), int(m.group(3) or 0)
            out.append((start, end if end >= start else None, 1))
    return out


def _named_in_question(symbols: list[_Sym], question_ids: set[str]) -> list[_Anchor]:
    """Definitions the question names exactly.

    A body-less declaration ranks lower, and so does a named class whose
    members the question also names: the members are what it asks about.
    """
    named = [s for s in symbols if s.name in question_ids or s.qualified in question_ids]
    owners = {s.parent.split(".")[-1] for s in named if s.parent}

    def rank(s: _Sym) -> int:
        if s.end <= s.start:
            return 4
        return 2 if s.kind in _CONTAINER_KINDS and s.name in owners else 1

    return [(s.start, s.end, rank(s)) for s in named]


def _matched_symbols(raw: dict[str, Any], path: str) -> list[_Anchor]:
    """Symbols retrieval hydrated for the question, and the guesses' listed functions."""
    out: list[_Anchor] = [
        (ks["start_line"], ks.get("end_line"), 3)
        for row in _rows_in(raw, "retrieval", path)
        for ks in row.get("key_symbols") or []
        if isinstance(ks, dict) and ks.get("source_excerpt") and isinstance(ks.get("start_line"), int)
    ]
    out += [
        (fn["line"], None, 5)
        for row in _rows_in(raw, "best_guesses", path)
        for fn in row.get("functions") or []
        if isinstance(fn, dict) and isinstance(fn.get("line"), int)
    ]
    return out


def _anchors(
    raw: dict[str, Any], path: str, question_ids: set[str], symbols: list[_Sym]
) -> list[_Anchor]:
    """Every anchor the reply holds in *path*, strongest first by rank."""
    out: list[_Anchor] = [
        (span[0], span[1], 0)
        for row in _rows_in(raw, "symbol_bodies", path)
        if (span := _span(row))
    ]
    out += _answer_refs(raw, path)
    out += [
        (span[0], None, 2)
        for key in ("quotes", "code_rationale")
        for row in _rows_in(raw, key, path)
        if (span := _span(row))
    ]
    out += _named_in_question(symbols, question_ids)
    return out + _matched_symbols(raw, path)


def _word_matched(symbols: list[_Sym], question: str) -> list[_Anchor]:
    """Definitions whose names carry the question's words, most words first.

    Keyless replies hold few anchors outside the top match; the names a file
    defines are where the question's words land.
    """
    words = question_stems(question)
    scored = [
        (len(words & question_stems(s.name)), s)
        for s in symbols
        if s.kind in _CODE_KINDS - _CONTAINER_KINDS
    ]
    # Ties go to the larger body: a substantive definition over a one-line binding.
    best = sorted((x for x in scored if x[0]), key=lambda x: (-x[0], x[1].start - x[1].end))
    return [(s.start, s.end, 2) for _, s in best[:_WORD_ANCHORS]]


def _fallback_anchor(symbols: list[_Sym], question: str) -> list[tuple[int, int | None, int]]:
    """With no anchor, the definition whose name shares most words with the question."""
    words = question_stems(question)
    best, best_hits = None, 0
    for sym in symbols:
        if sym.kind not in _CODE_KINDS:
            continue
        hits = len(words & question_stems(sym.name))
        if hits > best_hits:
            best, best_hits = sym, hits
    if best is None:
        best = next((s for s in symbols if s.kind in _CODE_KINDS - _CONTAINER_KINDS), None)
    return [(best.start, best.end, 6)] if best else []


def _innermost(symbols: list[_Sym], line: int) -> _Sym | None:
    """The smallest code symbol whose span holds *line*."""
    holding = [s for s in symbols if s.kind in _CODE_KINDS and s.start <= line <= s.end]
    return min(holding, key=lambda s: s.end - s.start) if holding else None


def _symbol_span(sym: _Sym, line: int, end: int | None, symbols: list[_Sym]) -> tuple[int, int]:
    """From the definition line when the anchor is near it, else from just above
    the anchor; on to the end of the symbol. A class window is its head: each
    member it holds is a window of its own."""
    start = sym.start if line - sym.start < _LEAD_LINES else line - _LEAD_LINES
    stop = max(end or 0, sym.end)
    inner = [s.start for s in symbols if s.kind in _CODE_KINDS and start < s.start <= sym.end]
    if sym.kind in _CONTAINER_KINDS and inner:
        stop = min(inner) - 1
    return start, stop


def _window_for(
    line: int, end: int | None, rank: int, symbols: list[_Sym], n_lines: int
) -> _Window:
    """The window an anchor opens: definition to end of symbol, at most the cap."""
    sym = _innermost(symbols, line)
    names: list[str] = []
    value = next((s for s in symbols if s.start == line and s.kind not in _CODE_KINDS), None)
    if value is not None and (sym is None or sym.kind in _CONTAINER_KINDS):
        # A constant or field: its own lines and the comment above them.
        start, stop = max(1, value.start - 3), value.end
        names = [value.qualified or value.name]
    elif sym is not None:
        start, stop = _symbol_span(sym, line, end, symbols)
        names = [sym.qualified or sym.name] if start == sym.start else []
    elif end is not None and end >= line:
        start, stop = line, end
    else:
        start, stop = max(1, line - _LEAD_LINES), line + WINDOW_MAX_LINES - _LEAD_LINES
    start = max(1, min(start, n_lines))
    stop = max(start, min(stop, start + WINDOW_MAX_LINES - 1, n_lines))
    return _Window(start, stop, rank, names)


def _merge(windows: list[_Window]) -> list[_Window]:
    """Merge windows that overlap or sit within the merge gap; keep the best rank."""
    merged: list[_Window] = []
    for w in sorted(windows, key=lambda w: (w.start, w.end)):
        # Overlapping windows always merge; near ones only at the same rank and
        # while the result stays one window long, so a weak anchor never rides
        # on a strong one's rank.
        near = (
            merged
            and w.rank == merged[-1].rank
            and w.start <= merged[-1].end + _MERGE_GAP + 1
            and max(merged[-1].end, w.end) - merged[-1].start < WINDOW_MAX_LINES
        )
        if merged and (w.start <= merged[-1].end or near):
            last = merged[-1]
            last.end = max(last.end, w.end)
            last.rank = min(last.rank, w.rank)
            last.names += [n for n in w.names if n not in last.names]
        else:
            merged.append(_Window(w.start, w.end, w.rank, list(w.names)))
    return merged


def _drifted(lines: list[str], symbols: list[_Sym], windows: list[_Window]) -> bool:
    """True when an anchored definition is not on its indexed line any more."""
    for w in windows:
        for sym in symbols:
            if sym.start == w.start and (sym.qualified in w.names or sym.name in w.names):
                # Decorators and attributes may sit between the indexed start and the name.
                head = " ".join(lines[max(0, sym.start - 1) : sym.start + 15])
                if sym.name.split(".")[-1] not in head:
                    return True
    return False


def _rendered_bytes(text: str) -> int:
    """UTF-8 bytes *text* takes inside a JSON string."""
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8")) - 2


def _render_lines(
    lines: list[str], start: int, end: int, prose: list[bool] | None = None
) -> str:
    """Lines *start*..*end* numbered like Read.

    With *prose* (a per-line docstring/comment mask), a run of more than
    :data:`_PROSE_RUN` such lines keeps its first :data:`_PROSE_KEEP` and a
    marker line stands for the rest: the code is what an answer rests on.
    """
    out: list[str] = []
    n = start
    while n <= end:
        if prose is not None and prose[n - 1]:
            run_end = n
            while run_end < end and prose[run_end]:
                run_end += 1
            if run_end - n + 1 > _PROSE_RUN:
                out += [f"{k}\t{lines[k - 1]}" for k in range(n, n + _PROSE_KEEP)]
                out.append(f"... lines {n + _PROSE_KEEP}-{run_end} {_PROSE_MARK}")
                n = run_end + 1
                continue
        out.append(f"{n}\t{lines[n - 1]}")
        n += 1
    return "\n".join(out)


_HASH_COMMENT_SUFFIXES = (".py", ".pyi", ".rb", ".sh", ".bash", ".pl", ".r", ".yaml", ".yml", ".toml")
_COMMENT_LEADS = ("//", "/*", "* ", "*/", "--")
_DOCSTRING_RE = re.compile("^[rRbBuU]{0,2}(\"{3}|'{3})")


def _prose_mask(path: str, lines: list[str]) -> list[bool]:
    """Which lines are comment or docstring text, by a line-level reading.

    Comment lines by their lead (``#`` only where it starts a comment, never a
    C preprocessor line); Python docstrings by their triple quote, through the
    line that closes it. Code that only looks like prose is left as code.
    """
    hash_comments = path.lower().endswith(_HASH_COMMENT_SUFFIXES)
    python = path.lower().endswith((".py", ".pyi"))
    mask = [False] * len(lines)
    quote: str | None = None
    for i, raw in enumerate(lines):
        text = raw.strip()
        if quote is not None:
            mask[i] = True
            if quote in text:
                quote = None
            continue
        if python and (m := _DOCSTRING_RE.match(text)):
            mask[i] = True
            delim = m.group(1)
            if text.count(delim) == 1:
                quote = delim
            continue
        mask[i] = text.startswith(_COMMENT_LEADS) or text == "*" or (
            hash_comments and text.startswith("#")
        )
    return mask


def _gap_marker(symbols: list[_Sym], lo: int, hi: int) -> tuple[str, list[str]]:
    """The marker line for lines *lo*..*hi* left out, naming definitions there."""
    inside = [s for s in symbols if lo <= s.start <= hi and s.kind in _CODE_KINDS]
    # Outermost first: a skipped class says more than each of its methods.
    inside.sort(key=lambda s: (s.parent is not None, s.start))
    names = [s.qualified or s.name for s in inside]
    shown = names[:_GAP_NAMES]
    more = f" +{len(names) - len(shown)} more" if len(names) > len(shown) else ""
    label = f": {', '.join(shown)}{more}" if shown else ""
    return f"... lines {lo}-{hi} not shown{label}", names


def _cut_definitions(symbols: list[_Sym], ranges: list[tuple[int, int]]) -> list[tuple[str, int]]:
    """``(name, last line sent)`` for each definition a sent range starts but does not finish.

    Read off the rendered ranges, so the statement names exactly what was cut,
    whether by the line cap or by the byte budget. Outermost first, two per range.
    """
    out: list[tuple[str, int]] = []
    for a, b in ranges:
        open_ = [s for s in symbols if s.kind in _CODE_KINDS and a <= s.start <= b < s.end]
        open_.sort(key=lambda s: (s.start, -s.end))
        out += [(s.qualified or s.name, b) for s in open_[:2]]
    return out


@dataclass
class _Served:
    path: str
    ranges: list[tuple[int, int]]
    code: str
    whole: bool
    cut: list[tuple[str, int]]  # (definition, last line sent)
    skipped: list[str]
    # Definitions the reply or question named that did not fit.
    held: list[str] = field(default_factory=list)


def _fit(plan: _FilePlan, w: _Window, room: int, *, first: bool) -> tuple[_Window | None, int]:
    """*w* whole, or cut at a whole line, within *room* bytes; ``(None, 0)`` when too little fits.

    A window after a file's first may bring a gap marker line, so it pays for one.
    """
    extra = 1 + (0 if first else _MARKER_BYTES)

    def cost(end: int) -> int:
        return _rendered_bytes(_render_lines(plan.lines, w.start, end, plan.prose)) + extra

    if cost(w.end) <= room:
        return w, cost(w.end)
    end = w.end - 1
    while end - w.start + 1 >= _MIN_CUT_LINES and cost(end) > room:
        end -= 1
    if end - w.start + 1 < _MIN_CUT_LINES:
        return None, 0
    return _Window(w.start, end, w.rank, w.names), cost(end)


def _render_file(plan: _FilePlan, chosen: list[_Window]) -> _Served:
    """The file's chosen windows in line order, with a marker line for each gap."""
    lines = plan.lines
    if plan.whole:
        return _Served(plan.path, [(1, len(lines))], _render_lines(lines, 1, len(lines)), True, [], [])
    chosen = sorted(chosen, key=lambda w: w.start)
    parts: list[str] = []
    skipped: list[str] = []
    prev_end = 0
    for w in chosen:
        if prev_end and w.start > prev_end + 1:
            marker, names = _gap_marker(plan.symbols, prev_end + 1, w.start - 1)
            parts.append(marker)
            skipped += names
        parts.append(_render_lines(lines, w.start, w.end, plan.prose))
        prev_end = w.end
    ranges = [(w.start, w.end) for w in chosen]
    return _Served(
        plan.path, ranges, "\n".join(parts), False, _cut_definitions(plan.symbols, ranges), skipped
    )


async def _symbols_by_file(ctx: Any, files: list[str]) -> dict[str, list[_Sym]]:
    """The indexed symbols of *files*, one query."""
    from sqlalchemy import select

    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server._helpers import _get_repo

    out: dict[str, list[_Sym]] = {f: [] for f in files}
    async with get_session(ctx.session_factory) as session:
        repo_id = (await _get_repo(session)).id
        res = await session.execute(
            select(
                WikiSymbol.file_path, WikiSymbol.name, WikiSymbol.qualified_name,
                WikiSymbol.kind, WikiSymbol.start_line, WikiSymbol.end_line,
                WikiSymbol.parent_name,
            ).where(WikiSymbol.repository_id == repo_id, WikiSymbol.file_path.in_(files))
        )
        for fp, name, qn, kind, start, end, parent in res.all():
            if isinstance(start, int) and start > 0:
                out.setdefault(_norm(fp), []).append(
                    _Sym(name, _short_qualified(qn, name, parent), kind, start, max(start, end or start), parent)
                )
    return out


def _short_qualified(qualified: str | None, name: str, parent: str | None) -> str:
    """``Owner.member`` for a member, the bare name otherwise."""
    if parent:
        return f"{parent.split('.')[-1]}.{name}"
    return name


def plan_files(
    raw: dict[str, Any], question: str, question_ids: set[str], root: Path,
    symbols: dict[str, list[_Sym]], files: list[str], *, words: bool = True,
) -> tuple[list[_FilePlan], list[str]]:
    """Read each file live and lay out its windows; also the files left out as changed."""
    plans: list[_FilePlan] = []
    drifted: list[str] = []
    for path in files:
        text = read_repo_file_text(root, path, allow_extracted=False)
        if text is None:
            continue
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            continue
        syms = symbols.get(path, [])
        anchors = _anchors(raw, path, question_ids, syms)
        anchors += _word_matched(syms, question) if words else []
        anchors = anchors or _fallback_anchor(syms, question)
        if not anchors:
            anchors = [(1, None, 7)]
        windows = _merge([_window_for(a, e, r, syms, len(lines)) for a, e, r in anchors])
        if _drifted(lines, syms, windows):
            plans.append(_FilePlan(path, lines, [], [], whole=True))
            drifted.append(path)
            continue
        plans.append(_FilePlan(path, lines, syms, windows, prose=_prose_mask(path, lines)))
    return plans, drifted


class _Allocation:
    """The windows chosen per file while a byte budget runs down."""

    def __init__(self, plans: list[_FilePlan], budget: int) -> None:
        self.plans = plans
        self.budget = budget
        self.left = budget
        self.chosen: dict[str, list[_Window]] = {p.path: [] for p in plans}
        self.whole: set[str] = set()
        self.taken: set[int] = set()  # ids of planned windows served, whole or cut

    def take(self, plan: _FilePlan, w: _Window, cap: int) -> None:
        got, cost = _fit(plan, w, min(self.left, cap), first=not self.chosen[plan.path])
        if got is not None:
            self.chosen[plan.path].append(got)
            self.taken.add(id(w))
            self.left -= cost

    def whole_files(self) -> None:
        for plan in (p for p in self.plans if p.whole):
            cost = _rendered_bytes(_render_lines(plan.lines, 1, len(plan.lines)))
            if cost <= self.left:
                self.whole.add(plan.path)
                self.left -= cost

    def named(self) -> None:
        """Every window the answer or question names, top file first, within the named share."""
        cap = int(self.budget * _NAMED_SHARE)
        named = [
            (i, p, w) for i, p in enumerate(self.plans) if not p.whole
            for w in p.windows if w.rank <= _NAMED_RANK
        ]
        # Several named definitions share the slot evenly; a cut one says where it stopped.
        each = max(cap // max(1, len(named)), _NAMED_MIN_BYTES)
        for _, plan, w in sorted(named, key=lambda x: (x[0], x[2].rank, x[2].start)):
            self.take(plan, w, min(each, self.left - (self.budget - cap)))

    def first_windows(self) -> None:
        """One short window for each further file, so the lead file cannot crowd it out."""
        for plan in self.plans[1:]:
            rest = [w for w in plan.windows if id(w) not in self.taken]
            if rest and not self.chosen[plan.path] and not plan.whole:
                self.take(plan, min(rest, key=lambda w: (w.rank, w.start)), _OTHER_FIRST_BYTES)

    def remaining(self) -> None:
        """What is left, file by file in rank order: agents read on in the lead file."""
        for plan in self.plans:
            for w in sorted(plan.windows, key=lambda w: (w.rank, w.start)):
                if id(w) not in self.taken and self.left > 400:
                    self.take(plan, w, self.left)

    def served(self) -> tuple[list[_Served], list[str]]:
        out: list[_Served] = []
        for plan in self.plans:
            if plan.path not in self.whole and not self.chosen[plan.path]:
                continue
            served = _render_file(plan, self.chosen[plan.path])
            served.held = [
                n for w in plan.windows
                if w.rank <= _NAMED_RANK and id(w) not in self.taken for n in w.names
            ]
            out.append(served)
        shown = {s.path for s in out}
        return out, [p.path for p in self.plans if p.path not in shown]


def allocate(plans: list[_FilePlan], budget: int) -> tuple[list[_Served], list[str]]:
    """Spine first: the definitions the reply names, then one window per file, then the rest."""
    alloc = _Allocation(plans, budget)
    alloc.whole_files()
    alloc.named()
    alloc.first_windows()
    alloc.remaining()
    return alloc.served()


def delivered_line(
    served: list[_Served], not_shown: list[str], drifted: list[str]
) -> tuple[str, list[str]]:
    """What was sent, computed from the render, and the exact names to ask for next."""
    parts: list[str] = []
    next_names: list[str] = []
    for s in served:
        if s.whole:
            parts.append(f"{s.path} whole (changed since the index, so not windowed)")
            continue
        spans = ", ".join(f"{a}-{b}" for a, b in s.ranges)
        notes = [f"{name} cut after line {end}" for name, end in s.cut]
        if s.held:
            notes.append(f"not sent: {', '.join(s.held)}")
        parts.append(f"{s.path} {spans}" + (f" ({'; '.join(notes)})" if notes else ""))
        next_names += [f"{s.path}::{name}" for name in s.held]
        next_names += [f"{s.path}::{name}" for name, _ in s.cut]
        next_names += [f"{s.path}::{name}" for name in s.skipped]
    text = (
        "Source below is the live file text, numbered like Read; the lines sent need "
        "no second read: " + "; ".join(parts) + "."
    )
    if any(_PROSE_MARK in s.code for s in served):
        text += f" Docstring and comment runs over {_PROSE_RUN} lines keep their first {_PROSE_KEEP}."
    omitted = [p for p in drifted if p not in {s.path for s in served}]
    if omitted:
        text += f" Changed since the index and too long to send whole: {', '.join(omitted)}."
    if not_shown:
        text += f" Ranked but not shown: {', '.join(not_shown)}."
    next_names = list(dict.fromkeys(next_names))[:_NEXT_NAMES]
    if next_names:
        text += (
            " More source: get_answer with only the names as the question, e.g. "
            f"\"{' '.join(next_names[:3])}\"."
        )
    return text, next_names


def render_source(served: list[_Served]) -> list[dict[str, Any]]:
    return [{"path": s.path, "code": s.code} for s in served]


@dataclass
class SourcePlan:
    """The windows laid out for a reply, before the byte budget is known."""

    plans: list[_FilePlan]
    drifted: list[str]
    # Ranked files with no window, best first: they lead ``candidate_files``.
    rest: list[str] = field(default_factory=list)

    def serve(self, budget: int) -> tuple[list[dict[str, Any]], str] | None:
        """``(source, delivered)`` within *budget* bytes, or None when nothing fits."""
        served, not_shown = allocate(self.plans, budget)
        if not served:
            return None
        text, _ = delivered_line(served, not_shown, self.drifted)
        # The statement shares the budget: lay out again with its bytes taken off
        # (and a margin, since a tighter layout can name one more file).
        over = _rendered_bytes(text) + 2 + _MARKER_BYTES
        served, not_shown = allocate(self.plans, max(budget - over, SOURCE_MIN_BYTES // 2))
        if not served:
            return None
        text, _ = delivered_line(served, not_shown, self.drifted)
        return render_source(served), text


async def plan_source(
    raw: dict[str, Any], question: str, root: Path | None, ctx: Any, *,
    files: list[str] | None = None, question_ids: set[str] | None = None,
) -> SourcePlan | None:
    """Read the reply's ranked files and lay out their windows, or None."""
    if root is None:
        return None
    leading, pool, scores = ranked_files(raw) if files is None else (files, [], {})
    if not leading and not pool:
        return None
    if question_ids is None:
        from repowise.server.mcp_server.tool_answer.symbols import _extract_question_identifiers

        question_ids = _extract_question_identifiers(question)
    try:
        symbols = await _symbols_by_file(ctx, [*leading, *pool])
    except Exception:  # windows still serve, anchored on the reply's own lines
        _log.warning("get_answer: symbol lookup for source windows failed", exc_info=True)
        symbols = {}
    ranked = _rerank(pool, question, symbols)
    admitted = files if files is not None else _admit(leading, ranked, scores)
    # A continuation asks for exact names only; question words would add others.
    plans, drifted = plan_files(
        raw, question, question_ids, root, symbols, admitted, words=files is None
    )
    if not plans:
        return None
    planned = {plan.path for plan in plans}
    unplanned = [p for p in [*admitted, *leading] if p not in planned]
    rest = list(dict.fromkeys([*unplanned, *(p for p, _ in ranked if p not in planned)]))
    return SourcePlan(plans, drifted, rest)


# --- continuation: a question that is only names ---------------------------

_NAME_TOKEN_RE = re.compile(r"^(?:(?P<path>[\w./\\-]+\.\w+)::)?(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)$")


def continuation_names(question: str) -> list[tuple[str | None, str]] | None:
    """``[(path or None, name)]`` when the question is only exact names, else None."""
    tokens = [t.strip("`'\",;") for t in re.split(r"[\s,]+", question.strip()) if t.strip("`'\",;")]
    if not tokens or len(tokens) > 8:
        return None
    out: list[tuple[str | None, str]] = []
    for tok in tokens:
        m = _NAME_TOKEN_RE.match(tok)
        if not m:
            return None
        path = _norm(m.group("path")) if m.group("path") else None
        name = m.group("name")
        # A bare lowercase word is English, not a name, unless it carries a path.
        if path is None and "." not in name and "_" not in name and name.islower():
            return None
        out.append((path, name))
    return out


async def resolve_names(
    ctx: Any, names: list[tuple[str | None, str]]
) -> list[tuple[str, int, int, str]] | None:
    """``(path, start, end, label)`` per name, or None when any name does not resolve.

    A name resolves only to exactly one definition, so a continuation never
    picks among same-named symbols; a path or owner narrows it.
    """
    from sqlalchemy import select

    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server._helpers import _get_repo

    found: list[tuple[str, int, int, str]] = []
    async with get_session(ctx.session_factory) as session:
        repo_id = (await _get_repo(session)).id
        for path, name in names:
            leaf = name.split(".")[-1]
            owner = name.split(".")[0] if "." in name else None
            q = select(
                WikiSymbol.file_path, WikiSymbol.start_line, WikiSymbol.end_line,
                WikiSymbol.parent_name,
            ).where(WikiSymbol.repository_id == repo_id, WikiSymbol.name == leaf)
            if path:
                q = q.where(WikiSymbol.file_path == path)
            rows = [
                r for r in (await session.execute(q)).all()
                if owner is None or (r[3] or "").split(".")[-1] == owner
            ]
            if len(rows) != 1:
                return None
            for fp, start, end, _ in rows:
                found.append((_norm(fp), int(start), int(max(start, end or start)), name))
    return found

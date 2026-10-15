"""Post-turn numeric check: each figure in an answer should come from a tool.

``check_figures`` reads every number in the final answer text, sets aside the
ones that are not figures (dates, years, versions, ``file:line`` citations,
list ordinals, identifiers and hashes, anything inside code, numbers the
question already holds) and looks each remaining one up among the numbers of
the turn's tool results and views, then, for a figure still unmatched, among
earlier turns' results. The lookup tolerates rounding to the precision the
answer shows, percent and fraction forms (``73%`` for ``0.734``), size and time
units (``1.2 MB``, ``250 ms``), ``k``/``M`` suffixes and thousands separators;
a hedged figure ("about 1,200", "24,000+") gets 10%. A signed figure (``-5``)
only matches a value of that sign; an unsigned one matches either.

A number right after a position or label word (``_LABEL_WORDS``: line, top,
first, level, tier, step, version...) is never checked, even when it is a real
count such as "the top 5 files". That is a deliberate false negative: those
words far more often introduce a position, rank or label than a figure, and a
missed check costs less than a false flag.

Reading evidence is bounded: past ``MAX_NODES_PER_ARTIFACT`` or
``MAX_CHARS_PER_ARTIFACT`` in one result, or ``MAX_NODES_TOTAL`` or
``MAX_CHARS_TOTAL`` overall, the rest is not read, and a figure that matched
nothing read counts as not checked rather than flagged.

A figure no result holds may still be worked out from figures that are
there, and is then ``derived`` rather than flagged, but only where the answer
shows the working: a percentage whose clause writes its two terms next to
each other ("254 of 505 (50.3%)", "7/8"), a count whose clause writes the sum
out ("320 + 115 = 435"), or a share column of a markdown table whose rows,
at least two of them, are their count over one total the answer names
("Total: 505"). Every term must itself be held by a result, matches use the
precision shown and never the hedge allowance, and no other pairing of the
answer's numbers counts: two matched numbers that merely divide to the figure
do not make it derived.

A figure with no match is flagged, never removed. A matched one carries the
``artifact_id`` of the tool result it came from, which is its citation.
Offsets are UTF-16 code units so a browser can slice the text with them.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

# A leading minus is a sign only when no word character or digit precedes it,
# so "10-20" stays a range and "utf-8" an identifier.
_NUMBER = re.compile(
    r"(?:(?<![A-Za-z0-9_])[-−])?"
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
)
_UNIT = re.compile(
    r" ?(%|percent\b|ms\b|milliseconds?\b|secs?\b|seconds?\b|s\b|mins?\b|minutes?\b"
    r"|hrs?\b|hours?\b|h\b|days?\b|[KMGT]i?B\b|kB\b|bytes?\b|B\b|k\b|K\b|M\b"
    r"|thousand\b|million\b|billion\b|x\b|×)",
    re.ASCII,
)

MAX_NODES_PER_ARTIFACT = 20_000
MAX_CHARS_PER_ARTIFACT = 256_000
MAX_NODES_TOTAL = 100_000
MAX_CHARS_TOTAL = 1_000_000

_KB, _MB, _GB, _TB = 1024.0, 1024.0**2, 1024.0**3, 1024.0**4
#: unit -> (family, multipliers to the family's base). Sizes read both 1024 and 1000.
_SCALES: dict[str, tuple[str, tuple[float, ...]]] = {
    "ms": ("time", (1.0,)),
    "s": ("time", (1e3,)),
    "min": ("time", (6e4,)),
    "h": ("time", (3.6e6,)),
    "day": ("time", (8.64e7,)),
    "B": ("size", (1.0,)),
    "KB": ("size", (_KB, 1e3)),
    "MB": ("size", (_MB, 1e6)),
    "GB": ("size", (_GB, 1e9)),
    "TB": ("size", (_TB, 1e12)),
    "k": ("count", (1e3,)),
    "M": ("count", (1e6,)),
    "bn": ("count", (1e9,)),
}
_UNIT_ALIASES = {
    "percent": "%",
    "millisecond": "ms",
    "milliseconds": "ms",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "mins": "min",
    "minute": "min",
    "minutes": "min",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "days": "day",
    "KiB": "KB",
    "kB": "KB",
    "MiB": "MB",
    "GiB": "GB",
    "TiB": "TB",
    "byte": "B",
    "bytes": "B",
    "K": "k",
    "thousand": "k",
    "million": "M",
    "billion": "bn",
    "×": "x",
}

# Spans whose numbers are never figures: code, links, dates, file:line, refs.
_MASKS = re.compile(
    r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|``[^`]*``|`[^`\n]*`"
    r"|https?://\S+|\]\([^)\s]*\)"
    r"|[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:\.[0-9]+)?Z?)?"
    r"|[\w./\\-]+\.[A-Za-z]\w*:[0-9]+(?:[-–:][0-9]+)?"
    r"|#[0-9]+|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b",
    re.DOTALL,
)
# The word just before a number that makes it a position, label or version.
# Each one also hides real counts ("top 5"): a deliberate false negative.
_LABEL_WORDS = frozenset(
    {
        "line", "lines", "row", "rows", "column", "col", "version", "versions", "v", "release",
        "python", "node", "java", "go", "ruby", "php", "typescript", "es", "schema", "revision",
        "migration", "alembic", "step", "steps", "phase", "stage", "section", "sections",
        "part", "item", "items", "chapter", "wave", "track", "lane", "option", "options",
        "rank", "top", "bottom", "first", "last", "no", "number", "pr", "prs", "issue",
        "issues", "pull", "tier", "level", "priority", "round", "iteration", "turn", "page",
        "pages", "figure", "table", "question", "point"
    }
)
_MONTHS = frozenset(
    {
        "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
        "dec", "january", "february", "march", "april", "june", "july", "august", "september",
        "october", "november", "december"
    }
)
_HEDGES = re.compile(
    r"(?:~|≈|<|>|≤|≥|\b(?:about|around|approximately|approx\.?|roughly|nearly"
    r"|almost|over|under|some|more than|less than|fewer than|at least|at most|up to"
    r"|close to))\s*$",
    re.IGNORECASE,
)
# What may precede a list ordinal on its line: indent, quote, heading, bullet, bold.
_LIST_MARKER = re.compile(r"[ \t]*(?:>[ \t]*)*(?:#{1,6}[ \t]+)?(?:[-*+][ \t]+)?(?:\*\*)?")
# Emphasis may sit between the word and the number ("line **485**").
_WORD_BEFORE = re.compile(r"([A-Za-z]+)\.?[ \t]*[*_]*$")
_WORD_AFTER = re.compile(r"[ \t]*([A-Za-z]+)")
_HEDGE_RELATIVE = 0.10


def _word_char(ch: str) -> bool:
    """An ASCII letter, digit or underscore: CJK around a number is prose, not an identifier."""
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _letter(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


@dataclass(frozen=True)
class Figure:
    """One number in an answer that the check looked up."""

    text: str
    start: int
    end: int
    verified: bool
    artifact_id: str | None = None
    #: Worked out from matched figures of the answer rather than read in a result.
    derived: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "verified": self.verified,
        }
        if self.artifact_id:
            out["artifact_id"] = self.artifact_id
        if self.derived:
            out["derived"] = True
        return out


@dataclass
class FigureCheck:
    """The figures looked up, and how many were left unchecked because the
    evidence was past the read cap."""

    figures: list[Figure] = field(default_factory=list)
    not_checked: int = 0

    def diagnostics(self) -> dict[str, int]:
        if not self.figures and not self.not_checked:
            return {}
        out = {
            "figures_checked": len(self.figures),
            "figures_unverified": sum(not f.verified for f in self.figures),
        }
        if derived := sum(f.derived for f in self.figures):
            out["figures_derived"] = derived
        if self.not_checked:
            out["figures_not_checked"] = self.not_checked
        return out


@dataclass(frozen=True)
class _Reading:
    value: float
    signed: bool
    decimals: int
    unit: str | None
    text_end: int


def _read(text: str, match: re.Match[str]) -> _Reading:
    raw = match.group().replace(",", "").replace("−", "-")
    decimals = len(raw.partition(".")[2])
    unit_match = _UNIT.match(text, match.end())
    if unit_match is None:
        unit, end = None, match.end()
    else:
        unit = _UNIT_ALIASES.get(unit_match.group(1), unit_match.group(1))
        end = unit_match.end()
    return _Reading(float(raw), raw.startswith("-"), decimals, unit, end)


def _values(reading: _Reading) -> list[float]:
    """The reading's value and its forms in the other units of its family."""
    value, unit = reading.value, reading.unit
    if unit == "%":
        return [value, value / 100]
    if unit not in _SCALES:
        return [value]
    family, multipliers = _SCALES[unit]
    out = [value]
    for mult in multipliers:
        base = value * mult
        out.append(base)
        if family in ("time", "size"):
            for other, (other_family, other_mults) in _SCALES.items():
                if other_family == family and other != unit:
                    out.extend(base / m for m in other_mults)
    return out


# ---------------------------------------------------------------------------
# Source pool
# ---------------------------------------------------------------------------


@dataclass
class _Budget:
    nodes: int = MAX_NODES_TOTAL
    chars: int = MAX_CHARS_TOTAL


class _Pool:
    """The numbers some evidence holds, sorted, with the artifact each came from.

    Raw values only, plus the two expansions an answer's own conversions do
    not cover: a source percent read as a fraction and a ``k``/``M`` suffix
    read in full, both only when the answer states a unitless figure.
    """

    def __init__(self, budget: _Budget, *, expand: bool) -> None:
        self._entries: list[tuple[float, int, str | None]] = []
        self._values: list[float] = []
        self._budget = budget
        self._expand = expand
        self.truncated = False

    def add_all(self, artifact_id: str | None, data: Any) -> None:
        nodes, chars = MAX_NODES_PER_ARTIFACT, MAX_CHARS_PER_ARTIFACT
        stack = [data]
        while stack:
            if nodes <= 0 or chars <= 0 or self._budget.nodes <= 0 or self._budget.chars <= 0:
                self.truncated = True
                return
            node = stack.pop()
            nodes -= 1
            self._budget.nodes -= 1
            if isinstance(node, bool) or node is None:
                continue
            if isinstance(node, (int, float)):
                if node == node:  # NaN is no figure
                    self._add(float(node), artifact_id)
            elif isinstance(node, str):
                chars -= len(node)
                self._budget.chars -= len(node)
                self._add_text(node, artifact_id)
            elif isinstance(node, dict):
                # A count the answer may state is often the size of a list or map.
                self._add(float(len(node)), artifact_id)
                stack.extend(reversed(node.values()))
            elif isinstance(node, (list, tuple)):
                self._add(float(len(node)), artifact_id)
                stack.extend(reversed(node))

    def _add_text(self, text: str, artifact_id: str | None) -> None:
        if not any(ch.isdigit() for ch in text):
            return
        for match in _NUMBER.finditer(text):
            reading = _read(text, match)
            self._add(reading.value, artifact_id)
            if self._expand and reading.unit == "%":
                self._add(reading.value / 100, artifact_id)
            elif self._expand and reading.unit in ("k", "M", "bn"):
                self._add(reading.value * _SCALES[reading.unit][1][0], artifact_id)

    def _add(self, value: float, artifact_id: str | None) -> None:
        self._entries.append((value, len(self._entries), artifact_id))

    def freeze(self) -> _Pool:
        self._entries.sort()
        self._values = [entry[0] for entry in self._entries]
        return self

    def find(self, value: float, tolerance: float) -> str | None | bool:
        """The matching artifact id (``None`` when it has none), or ``False``."""
        slack = tolerance + 1e-9 * max(1.0, abs(value))
        i = bisect.bisect_left(self._values, value - slack)
        if i < len(self._values) and self._values[i] <= value + slack:
            return self._entries[i][2]
        return False


def _pool(sources: Iterable[tuple[str | None, Any]], budget: _Budget, expand: bool) -> _Pool:
    pool = _Pool(budget, expand=expand)
    for artifact_id, data in sources:
        pool.add_all(artifact_id, data)
    return pool.freeze()


# ---------------------------------------------------------------------------
# Answer side
# ---------------------------------------------------------------------------


def _masked(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _MASKS.finditer(text)]


def _inside(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    i = bisect.bisect_right(spans, (start, float("inf"))) - 1
    return i >= 0 and spans[i][0] <= start and end <= spans[i][1]


def _not_a_figure(text: str, start: int, reading: _Reading, previous_skipped_end: int) -> bool:
    # A space stands in for the text's edges so every test below is a plain char test.
    before = text[start - 1] if start else " "
    before2 = text[start - 2] if start > 1 else " "
    end = reading.text_end
    after = text[end] if end < len(text) else " "
    after2 = text[end + 1] if end + 1 < len(text) else " "

    if _letter(before) or before in "_/\\#@":
        return True
    if before == "." and _word_char(before2):
        return True
    if before == ":" and not before2.isspace():
        return True
    if before in "-–" and _letter(before2):
        return True
    # The far end of a range whose near end was not a figure ("lines 10-20").
    if before in "-–" and previous_skipped_end == start - 1:
        return True
    if reading.unit is None:
        if _letter(after) or after in "_/\\":
            return True
        if after in ".:" and _word_char(after2):
            return True
        if (before, after) in (("[", "]"), ("(", ")")):
            return True
    if (
        reading.unit is None
        and reading.decimals == 0
        and not reading.signed
        and after in ".)"
        and after2.isspace()
        and _LIST_MARKER.fullmatch(text, text.rfind("\n", 0, start) + 1, start)
    ):
        return True
    if reading.unit in (None, "s") and reading.decimals == 0 and 1900 <= reading.value <= 2099:
        return True
    word = _WORD_BEFORE.search(text, max(0, start - 24), start)
    if word and word.group(1).lower() in _LABEL_WORDS | _MONTHS:
        return True
    if reading.unit is None and reading.decimals == 0 and 0 <= reading.value <= 31:
        following = _WORD_AFTER.match(text, end)
        if following and following.group(1).lower() in _MONTHS:
            return True
    return False


def _tolerance(text: str, start: int, reading: _Reading) -> float:
    """Half a unit of the last digit shown; a hedged figure ("about 1,200",
    "24,000+") also gets 10% and its round zeros."""
    tolerance = 0.5 * 10.0**-reading.decimals
    if _HEDGES.search(text, max(0, start - 16), start) or text.startswith("+", reading.text_end):
        magnitude = abs(reading.value)
        if reading.decimals == 0:
            digits = str(int(magnitude))
            zeros = len(digits) - len(digits.rstrip("0")) if magnitude else 0
            tolerance = max(tolerance, 0.5 * 10.0**zeros)
        tolerance = max(tolerance, _HEDGE_RELATIVE * magnitude)
    return tolerance


def _find(pool: _Pool, reading: _Reading, tolerance: float) -> str | None | bool:
    """Look *reading* up in each of its units; an unsigned figure also matches a negative value."""
    for value in _values(reading):
        scale = abs(value / reading.value) if reading.value else 1.0
        for candidate in (value,) if reading.signed or not value else (value, -value):
            found = pool.find(candidate, tolerance * scale)
            if found is not False:
                return found
    return False


_NUM_TEXT = r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?"
# A clause ends at a sentence stop, a semicolon, a colon or a line break.
_CLAUSE_BREAK = re.compile(r"(?<=[.!?])\s+|[;\n]|:\s")
# A ratio's two terms, next to each other: "254 of 505", "3 out of 4", "7/8".
_PAIR = re.compile(rf"(?<![\w.])({_NUM_TEXT})\s*(?:out of|of|/)\s*({_NUM_TEXT})", re.IGNORECASE)
# A sum written out: "320 + 115 = 435", "2 + 3, 5 in all".
_SUM = re.compile(
    rf"((?:(?:{_NUM_TEXT})\s*\+\s*)+(?:{_NUM_TEXT}))\s*"
    rf"(?:(?:=|,?\s*(?:for a total of|a total of|in total|in all|total of|total|across)\s*)\s*({_NUM_TEXT})"
    rf"|,?\s*({_NUM_TEXT})\s+(?:in all|in total|total)\b)",
    re.IGNORECASE,
)
# A figure the answer names as a total: "Total: 505", "505 in all".
_TOTAL = re.compile(
    rf"\b(?:total|in all|across)\b\W{{0,3}}({_NUM_TEXT})|({_NUM_TEXT})\s+(?:in all|in total|total)\b",
    re.IGNORECASE,
)
_PERCENT_CELL = re.compile(rf"^({_NUM_TEXT})\s?%$")
_COUNT_CELL = re.compile(rf"^({_NUM_TEXT})$")


def _value(text: str) -> tuple[float, int]:
    raw = text.replace(",", "")
    return float(raw), len(raw.partition(".")[2])


class _Held:
    """Whether a number the answer writes is one the tool results hold, at its shown precision."""

    def __init__(self, pools: list[_Pool], more: Any) -> None:
        self._pools = pools
        self._more = more  # builds the earlier turns' pool on first need

    def __call__(self, text: str) -> bool:
        value, decimals = _value(text)
        reading = _Reading(value, False, decimals, None, 0)
        tolerance = 0.5 * 10.0**-decimals
        if any(_find(pool, reading, tolerance) is not False for pool in self._pools):
            return True
        extra = self._more()
        return extra is not None and _find(extra, reading, tolerance) is not False


def _close(value: float, shown: float, decimals: int) -> bool:
    return abs(value - shown) <= 0.5 * 10.0**-decimals + 1e-9


def _clauses(answer: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _CLAUSE_BREAK.finditer(answer):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(answer)))
    return spans


def _derived_spans(answer: str, held: _Held) -> set[int]:
    """Start offsets of figures worked out from held figures the answer states.

    A percentage is derived when its clause writes its two terms next to each
    other ("N of M", "N out of M", "N/M"); a count when its clause writes the
    sum out ("a + b = c", "a + b, c in all"); and a share column of a table
    when at least two of its rows are their count over one total the answer
    names. Every term must be held; no other pairing of numbers counts.
    """
    out: set[int] = set()
    for lo, hi in _clauses(answer):
        clause = answer[lo:hi]
        out.update(lo + at for at in _ratio_spans(clause, held))
        out.update(lo + at for at in _sum_spans(clause, held))
    return out | _table_shares(answer, held)


def _ratio_spans(clause: str, held: _Held) -> Iterator[int]:
    """Percentages in *clause* that one of its written-out ratios gives."""
    pairs = [
        (_value(m.group(1))[0], _value(m.group(2))[0])
        for m in _PAIR.finditer(clause)
        if held(m.group(1)) and held(m.group(2))
    ]
    if not pairs:
        return
    for m in _NUMBER.finditer(clause):
        if not clause.startswith(("%", " %"), m.end()):
            continue
        shown, decimals = _value(m.group())
        if any(0 < a <= b and _close(100.0 * a / b, shown, decimals) for a, b in pairs):
            yield m.start()


def _sum_spans(clause: str, held: _Held) -> Iterator[int]:
    """Totals in *clause* whose written-out parts are held and add up to them."""
    for m in _SUM.finditer(clause):
        parts = re.findall(_NUM_TEXT, m.group(1))
        group = 2 if m.group(2) else 3
        total, _ = _value(m.group(group))
        if all(held(p) for p in parts) and sum(_value(p)[0] for p in parts) == total:
            yield m.start(group)


def _named_totals(answer: str, held: _Held) -> set[float]:
    """Held figures the answer names as a total."""
    return {
        _value(t)[0]
        for m in _TOTAL.finditer(answer)
        if (t := m.group(1) or m.group(2)) and held(t)
    }


_Row = list[tuple[int, str]]


def _markdown_tables(answer: str) -> Iterator[list[_Row]]:
    """Each markdown table's rows as ``(offset, text)`` cells, separator rows left out."""
    table: list[_Row] = []
    offset = 0
    for line in answer.split("\n"):
        start, offset = offset, offset + len(line) + 1
        stripped = line.strip()
        if not stripped.startswith("|"):
            if table:
                yield table
            table = []
            continue
        if not set(stripped) <= set("|-: "):
            table.append(_cells(line, start))
    if table:
        yield table


def _cells(line: str, start: int) -> _Row:
    cells, pos = [], start + line.index("|") + 1
    for cell in line.strip().strip("|").split("|"):
        cells.append((pos + len(cell) - len(cell.lstrip()), cell.strip()))
        pos += len(cell) + 1
    return cells


def _column_hits(table: list[_Row], total: float, held: _Held) -> dict[int, list[int]]:
    """Per column, the share cells that are their row's count over *total*."""
    hits: dict[int, list[int]] = {}
    for row in table:
        counts = [_value(m.group(1))[0] for _, c in row if (m := _COUNT_CELL.match(c)) and held(c)]
        for col, (at, cell) in enumerate(row):
            if (pm := _PERCENT_CELL.match(cell)) is None:
                continue
            shown, decimals = _value(pm.group(1))
            if any(0 < c <= total and _close(100.0 * c / total, shown, decimals) for c in counts):
                hits.setdefault(col, []).append(at)
    return hits


def _table_shares(answer: str, held: _Held) -> set[int]:
    """Share cells of markdown tables that are their row's count over a named total."""
    totals = _named_totals(answer, held)
    out: set[int] = set()
    for table in _markdown_tables(answer) if totals else ():
        for total in totals:
            # One row agreeing with a total is a coincidence a small table makes easily.
            for starts in _column_hits(table, total, held).values():
                if len(starts) >= 2:
                    out.update(starts)
    return out


def _utf16_offsets(text: str) -> list[int] | None:
    """Code-point index -> UTF-16 offset, or None when every character is in the BMP."""
    if all(ord(ch) < 0x10000 for ch in text):
        return None
    offsets, total = [], 0
    for ch in text:
        offsets.append(total)
        total += 2 if ord(ch) >= 0x10000 else 1
    offsets.append(total)
    return offsets


def _candidates(answer: str, question: str) -> list[tuple[int, _Reading]]:
    """The answer's figures: numbers that are not labels, code or the question's own."""
    asked = {abs(_read(question, m).value) for m in _NUMBER.finditer(question)}
    masks = _masked(answer)
    out: list[tuple[int, _Reading]] = []
    skipped_end = -1
    for match in _NUMBER.finditer(answer):
        start = match.start()
        reading = _read(answer, match)
        if _inside(masks, start, match.end()) or _not_a_figure(answer, start, reading, skipped_end):
            skipped_end = match.end()
            continue
        if abs(reading.value) not in asked:
            out.append((start, reading))
    return out


class _Lookup:
    """This turn's evidence, then earlier turns' read only on first need."""

    def __init__(self, sources: Iterable[Any], earlier: Iterable[Any], expand: bool) -> None:
        self._budget = _Budget()
        self._expand = expand
        self._earlier_sources = list(earlier)
        self.pools = [_pool(sources, self._budget, expand)]

    def earlier(self) -> _Pool | None:
        if not self._earlier_sources:
            return None
        if len(self.pools) == 1:
            self.pools.append(_pool(self._earlier_sources, self._budget, self._expand))
        return self.pools[1]

    def find(self, reading: _Reading, tolerance: float) -> str | None | bool:
        found = _find(self.pools[0], reading, tolerance)
        if found is False and (extra := self.earlier()) is not None:
            found = _find(extra, reading, tolerance)
        return found

    @property
    def truncated(self) -> bool:
        return any(pool.truncated for pool in self.pools)


def check_figures(
    answer: str,
    sources: Iterable[tuple[str | None, Any]],
    question: str = "",
    earlier: Iterable[tuple[str | None, Any]] = (),
) -> FigureCheck:
    """Look up every figure in *answer* among the numbers of *sources*, then
    the still-unmatched ones among *earlier*.

    Each source is ``(artifact_id, data)``: a tool result or view of this turn
    for *sources*, a tool result of an earlier turn for *earlier*. Pure and
    CPU-bound; a caller on an event loop runs it in a thread.
    """
    check = FigureCheck()
    if not answer or not any(ch.isdigit() for ch in answer):
        return check
    candidates = _candidates(answer, question)
    if not candidates:
        return check

    lookup = _Lookup(sources, earlier, expand=any(r.unit is None for _, r in candidates))
    looked = [
        (start, reading, lookup.find(reading, _tolerance(answer, start, reading)))
        for start, reading in candidates
    ]
    derivable: set[int] = set()
    if any(found is False for _, _, found in looked):
        derivable = _derived_spans(answer, _Held(lookup.pools[:1], lookup.earlier))
    utf16 = _utf16_offsets(answer)
    for start, reading, found in looked:
        derived = found is False and not reading.signed and start in derivable
        if found is False and not derived and lookup.truncated:
            check.not_checked += 1
            continue
        end = reading.text_end
        check.figures.append(
            Figure(
                text=answer[start:end],
                start=utf16[start] if utf16 else start,
                end=utf16[end] if utf16 else end,
                verified=found is not False or derived,
                artifact_id=found if isinstance(found, str) else None,
                derived=derived,
            )
        )
    return check

"""Post-turn numeric check: each figure in an answer should come from a tool.

``check_figures`` reads every number in the final answer text, sets aside the
ones that are not figures (dates, years, versions, ``file:line`` citations,
list ordinals, identifiers and hashes, anything inside code, numbers the
question already holds) and looks each remaining one up among the numbers of
the turn's tool results and views. The lookup tolerates rounding to the
precision the answer shows, percent and fraction forms (``73%`` for ``0.734``),
size and time units (``1.2 MB``, ``250 ms``), ``k``/``M`` suffixes and
thousands separators; a hedged figure ("about 1,200") gets 10%.

A figure with no match is flagged, never removed. A matched one carries the
``artifact_id`` of the tool result it came from, which is its citation.
Offsets are UTF-16 code units so a browser can slice the text with them.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_UNIT = re.compile(
    r" ?(%|percent\b|ms\b|milliseconds?\b|secs?\b|seconds?\b|s\b|mins?\b|minutes?\b"
    r"|hrs?\b|hours?\b|h\b|days?\b|[KMGT]i?B\b|kB\b|bytes?\b|B\b|k\b|K\b|M\b"
    r"|thousand\b|million\b|billion\b|x\b|×)"
)

_KB, _MB, _GB, _TB = 1024.0, 1024.0**2, 1024.0**3, 1024.0**4
#: unit -> (family, multiplier to the family's base). Sizes read both 1024 and 1000.
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
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?"
    r"|[\w./\\-]+\.[A-Za-z]\w*:\d+(?:[-–:]\d+)?"
    r"|#\d+|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b",
    re.DOTALL,
)
# The word just before a number that makes it a position, label or version.
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
    r"(?:~|≈|<|>|≤|≥|\b(?:about|around|approximately|approx\.?|roughly|nearly|almost|over"
    r"|under|some|more than|less than|fewer than|at least|at most|up to|close to))\s*$",
    re.IGNORECASE,
)
# What may precede a list ordinal on its line: indent, quote, heading, bullet, bold.
_LIST_MARKER = re.compile(r"[ \t]*(?:>[ \t]*)*(?:#{1,6}[ \t]+)?(?:[-*+][ \t]+)?(?:\*\*)?")
# Emphasis may sit between the word and the number ("line **485**").
_WORD_BEFORE = re.compile(r"([A-Za-z]+)\.?[ \t]*[*_]*$")
_WORD_AFTER = re.compile(r"[ \t]*([A-Za-z]+)")
_HEDGE_RELATIVE = 0.10


@dataclass(frozen=True)
class Figure:
    """One number in an answer that the check looked up."""

    text: str
    start: int
    end: int
    verified: bool
    artifact_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "verified": self.verified,
        }
        if self.artifact_id:
            out["artifact_id"] = self.artifact_id
        return out


@dataclass(frozen=True)
class _Reading:
    value: float
    decimals: int
    unit: str | None
    text_end: int


def _unit(text: str, end: int) -> tuple[str | None, int]:
    match = _UNIT.match(text, end)
    if match is None:
        return None, end
    unit = match.group(1)
    return _UNIT_ALIASES.get(unit, unit), match.end()


def _read(text: str, match: re.Match[str]) -> _Reading:
    raw = match.group().replace(",", "")
    decimals = len(raw.partition(".")[2])
    unit, end = _unit(text, match.end())
    return _Reading(float(raw), decimals, unit, end)


def _values(reading: _Reading) -> list[float]:
    """The reading's value and its forms in other units of its family."""
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


class _Pool:
    """Every number the turn's evidence holds, sorted, with the artifact it came from."""

    def __init__(self) -> None:
        self._entries: list[tuple[float, int, str | None]] = []
        self._values: list[float] = []

    def add_all(self, artifact_id: str | None, data: Any) -> None:
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, bool) or node is None:
                continue
            if isinstance(node, (int, float)):
                if node == node:  # NaN is no figure
                    self._add(abs(float(node)), artifact_id)
            elif isinstance(node, str):
                for match in _NUMBER.finditer(node):
                    for value in _values(_read(node, match)):
                        self._add(value, artifact_id)
            elif isinstance(node, dict):
                # A count the answer may state is often the size of a list or map.
                self._add(float(len(node)), artifact_id)
                stack.extend(node.values())
            elif isinstance(node, (list, tuple)):
                self._add(float(len(node)), artifact_id)
                stack.extend(node)

    def _add(self, value: float, artifact_id: str | None) -> None:
        self._entries.append((value, len(self._entries), artifact_id))

    def freeze(self) -> None:
        self._entries.sort()
        self._values = [entry[0] for entry in self._entries]

    def find(self, value: float, tolerance: float) -> tuple[bool, str | None]:
        slack = tolerance + 1e-9 * max(1.0, value)
        i = bisect.bisect_left(self._values, value - slack)
        if i < len(self._values) and self._values[i] <= value + slack:
            return True, self._entries[i][2]
        return False, None


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

    if before.isalpha() or before in "_/\\#@":
        return True
    if before == "." and before2.isalnum():
        return True
    if before == ":" and not before2.isspace():
        return True
    if before in "-–" and before2.isalpha():
        return True
    # The far end of a range whose near end was not a figure ("lines 10-20").
    if before in "-–" and previous_skipped_end == start - 1:
        return True
    if reading.unit is None:
        if after.isalpha() or after in "_/\\":
            return True
        if after in ".:" and after2.isalnum():
            return True
        if (before, after) in (("[", "]"), ("(", ")")):
            return True
    if (
        reading.unit is None
        and reading.decimals == 0
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
    if reading.unit is None and reading.decimals == 0 and reading.value <= 31:
        following = _WORD_AFTER.match(text, end)
        if following and following.group(1).lower() in _MONTHS:
            return True
    return False


def _tolerance(text: str, start: int, reading: _Reading) -> float:
    """Half a unit of the last digit shown; a hedged figure ("about 1,200",
    "24,000+") also gets 10% and its round zeros."""
    tolerance = 0.5 * 10.0**-reading.decimals
    if _HEDGES.search(text, max(0, start - 16), start) or text.startswith("+", reading.text_end):
        if reading.decimals == 0:
            digits = str(int(reading.value))
            zeros = len(digits) - len(digits.rstrip("0")) if reading.value else 0
            tolerance = max(tolerance, 0.5 * 10.0**zeros)
        tolerance = max(tolerance, _HEDGE_RELATIVE * reading.value)
    return tolerance


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


def check_figures(
    answer: str,
    sources: Iterable[tuple[str | None, Any]],
    question: str = "",
) -> list[Figure]:
    """Look up every figure in *answer* among the numbers of *sources*.

    *sources* is ``(artifact_id, data)`` per tool result or view of the turn.
    """
    if not answer or not any(ch.isdigit() for ch in answer):
        return []
    pool = _Pool()
    for artifact_id, data in sources:
        pool.add_all(artifact_id, data)
    pool.freeze()
    asked = {_read(question, m).value for m in _NUMBER.finditer(question)}
    masks = _masked(answer)
    utf16 = _utf16_offsets(answer)

    figures: list[Figure] = []
    skipped_end = -1
    for match in _NUMBER.finditer(answer):
        start = match.start()
        reading = _read(answer, match)
        end = reading.text_end
        if _inside(masks, start, match.end()) or _not_a_figure(
            answer, start, reading, skipped_end
        ):
            skipped_end = match.end()
            continue
        if reading.value in asked:
            continue
        tolerance = _tolerance(answer, start, reading)
        verified, artifact_id = False, None
        for value in _values(reading):
            scale = value / reading.value if reading.value else 1.0
            verified, artifact_id = pool.find(value, tolerance * scale)
            if verified:
                break
        figures.append(
            Figure(
                text=answer[start:end],
                start=utf16[start] if utf16 else start,
                end=utf16[end] if utf16 else end,
                verified=verified,
                artifact_id=artifact_id,
            )
        )
    return figures

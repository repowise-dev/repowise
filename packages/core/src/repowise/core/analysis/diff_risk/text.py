"""Line views and line arithmetic over one file's text. No parse, no IO.

A line counts as added when its trimmed text occurs more often in head than
in base, so a moved line is not added. Markers are counted the same way
(see :func:`.detect._net`), so editing a line that already carried a
suppression does not read as a new one.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

from ..security.masking import mask_comments_and_strings, mask_dialect, source_lines
from .markers import HASH_COMMENT_LANGUAGES, Marker

#: Code characters in the comment view: never part of a marker match.
_HIDDEN = "\x00"

#: A Rust lifetime (``'a``, ``'static``), which the masker would read as a
#: string opener. A char literal (``'a'``) is left alone.
_RUST_LIFETIME = re.compile(r"'(?=[A-Za-z_]\w*(?![\w']))")

#: Line comment openers of TOML, INI, YAML and JS config files.
_CONFIG_COMMENT = ("#", ";", "//", "/*", "*")

_FENCE = re.compile(r"^\s*(?:```|~~~)")
_CODE_SPAN = re.compile(r"(`+).+?\1")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")


@dataclass(frozen=True, slots=True)
class Hit:
    """One marker occurrence on a 1-based line."""

    line: int
    label: str
    #: What a hit is counted by. The label, or for a suppression the comment
    #: text from the marker on, so a widened suppression is a new one.
    key: str


class FileView:
    """The text of one side of one file, with per-line views."""

    def __init__(self, text: str, path: str, language: str | None, *, prose: bool = False) -> None:
        self.text = text
        self.path = path
        self.language = language
        self.prose = prose
        self.lines = source_lines(text)
        self._local: dict[int, tuple[str, str]] = {}

    @cached_property
    def text_view(self) -> list[str]:
        """Comments blanked, strings kept."""
        if self.prose:
            return [""] * len(self.lines)
        return source_lines(self._mask(strings=False))

    @cached_property
    def code_view(self) -> list[str]:
        """Comments and strings blanked."""
        if self.prose:
            return [""] * len(self.lines)
        return source_lines(self._mask(strings=True))

    @cached_property
    def comment_view(self) -> list[str]:
        """Only comment text; every code or string character is hidden."""
        if self.prose:
            return _prose_comments(self.lines)
        # A marker quoted in a code span is documented, not applied.
        return [
            _hide_code_spans(
                "".join(
                    c if (c != m or c.isspace()) else _HIDDEN
                    for c, m in zip(raw, masked, strict=False)
                )
            )
            for raw, masked in zip(self.lines, self.text_view, strict=False)
        ]

    def _mask(self, *, strings: bool) -> str:
        source = self.text
        if self.language == "rust":
            source = _RUST_LIFETIME.sub(" ", source)
        return mask_comments_and_strings(
            source,
            strings=strings,
            hash_comments=self.language in HASH_COMMENT_LANGUAGES,
            **mask_dialect(self.path),
        )

    def local(self, index: int) -> tuple[str, str]:
        """``(text view, code view)`` of line *index* masked on its own.

        The whole-file mask carries string state across lines, so one quote
        it misreads (an apostrophe in a JS regex literal) shifts every later
        line. A hit must also hold on its own line, which no earlier line can
        throw off; the cost is a miss inside a multi-line string, never a
        false alarm.
        """
        if index not in self._local:
            alone = FileView(self.lines[index], self.path, self.language)
            self._local[index] = (alone.text_view[0], alone.code_view[0])
        return self._local[index]

    def is_code(self, index: int, at: int) -> bool:
        """Whether column *at* of line *index* is code in both masks."""
        raw = self.lines[index][at]
        if self.text_view[index][at] != raw or self.code_view[index][at] != raw:
            return False
        text, code = self.local(index)
        return text[at] == raw and code[at] == raw

    def code_hits(self, markers: Iterable[Marker]) -> list[Hit]:
        """Matches on the text view that start in code, not in a string."""
        hits: list[Hit] = []
        for marker in markers:
            for index, line in enumerate(self.text_view):
                if any(self.is_code(index, x.start()) for x in marker.pattern.finditer(line)):
                    hits.append(Hit(index + 1, marker.label, marker.label))
        return hits

    def comment_hits(self, markers: Iterable[Marker]) -> list[Hit]:
        """Suppression markers that open a comment, keyed by their text."""
        hits: list[Hit] = []
        for index, line in enumerate(self.comment_view):
            if not line.strip(_HIDDEN + " \t"):
                continue
            for marker in markers:
                match = marker.pattern.search(line)
                if match is not None and not self._in_local_string(index, match.start()):
                    hits.append(Hit(index + 1, marker.label, _marker_text(line, match.start())))
        return hits

    def raw_hits(self, markers: Iterable[Marker], *, comment: bool) -> list[Hit]:
        """Matches on the raw lines, with no mask at all.

        The base side of a change counts this way: a base marker the mask
        missed would make an unchanged head marker read as added, so base
        errs toward seeing more, which only makes a rule abstain.
        """
        found = (
            (index, line, marker, marker.pattern.search(line))
            for index, line in enumerate(self.lines)
            for marker in markers
        )
        return [
            Hit(index + 1, marker.label, _marker_text(line, x.start()) if comment else marker.label)
            for index, line, marker, x in found
            if x is not None
        ]

    def _in_local_string(self, index: int, at: int) -> bool:
        if self.prose:
            return False
        text, code = self.local(index)
        return text[at] != code[at]

    def count(self, markers: Iterable[Marker]) -> int:
        """Occurrences of *markers* in code, every match on a line counted."""
        markers = list(markers)
        return sum(
            1
            for index, line in enumerate(self.text_view)
            for marker in markers
            for x in marker.pattern.finditer(line)
            if self.is_code(index, x.start())
        )

    def local_count(self, markers: Iterable[Marker], lines: Iterable[int]) -> int:
        """Occurrences of *markers* on *lines*, each line masked on its own.

        For lines the change added or removed, where the whole-file mask of
        one side may have drifted while the other's did not.
        """
        markers = list(markers)
        total = 0
        for index in lines:
            text, code = self.local(index)
            total += sum(
                1
                for marker in markers
                for x in marker.pattern.finditer(text)
                if code[x.start()] == text[x.start()]
            )
        return total

    def identifiers(self, lines: Iterable[int] | None = None) -> set[str]:
        """Identifier tokens in code (0-based *lines*, or every line)."""
        view = self.code_view
        indexes = range(len(view)) if lines is None else lines
        return {token for i in indexes for token in _IDENTIFIER.findall(view[i])}


def _marker_text(line: str, start: int) -> str:
    """The comment text from *start*, whitespace collapsed, closers dropped."""
    text = line[start:].split(_HIDDEN, 1)[0]
    for closer in ("*/", "-->"):
        text = text.split(closer, 1)[0]
    return " ".join(text.split())


def _prose_comments(lines: list[str]) -> list[str]:
    """The HTML comments of Markdown lines; everything else is hidden.

    A marker quoted in a fence or a backtick span is documented, not applied,
    and one in plain prose (a heading, a bullet) applies to nothing.
    """
    out: list[str] = []
    fenced = in_comment = False
    for raw in lines:
        fence = not in_comment and _FENCE.match(raw)
        fenced ^= bool(fence)
        if fence or fenced:
            out.append("")
            continue
        line, in_comment = _html_comments(_hide_code_spans(raw), in_comment)
        out.append(line)
    return out


def _html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """*line* with everything outside ``<!-- -->`` hidden, and whether a
    comment is still open at its end."""
    kept: list[str] = []
    at = 0
    while at < len(line):
        if in_comment:
            close = line.find("-->", at)
            stop = len(line) if close == -1 else close + 3
            kept.append(line[at:stop])
            in_comment = close == -1
        else:
            opened = line.find("<!--", at)
            stop = len(line) if opened == -1 else opened
            kept.append(_HIDDEN * (stop - at))
            in_comment = opened != -1
        at = stop
    return "".join(kept), in_comment


def _hide_code_spans(line: str) -> str:
    return _CODE_SPAN.sub(lambda m: _HIDDEN * len(m.group()), line) if "`" in line else line


def added_line_indexes(base: list[str], head: list[str]) -> list[int]:
    """0-based indexes of *head* lines that *base* does not hold.

    Lines compare by trimmed text as a multiset; when head has more copies
    than base, the last copies count as added. Blank lines never count.
    """
    kept = Counter(line.strip() for line in base)
    head_counts = Counter(line.strip() for line in head)
    keep = {text: min(n, kept.get(text, 0)) for text, n in head_counts.items()}
    added: list[int] = []
    for index, line in enumerate(head):
        text = line.strip()
        if not text:
            continue
        if keep.get(text, 0) > 0:
            keep[text] -= 1
            continue
        added.append(index)
    return added


def numeric_values(text: str, key: str) -> list[tuple[float, int]]:
    """Every number assigned to *key* (``key: 90``, ``key = 0.8``, ``"key": 90``).

    The key must stand alone, so ``lines`` does not match ``maxLines``, and
    a commented-out line does not count. Returns ``(value, 1-based line)`` in
    file order.
    """
    pattern = re.compile(
        r"(?<![\w-])" + re.escape(key) + r"""(?![\w-])["']?\s*[:=]\s*["']?(-?\d+(?:\.\d+)?)"""
    )
    return [
        (float(m.group(1)), number)
        for number, line in enumerate(source_lines(text), 1)
        if not line.lstrip().startswith(_CONFIG_COMMENT)
        for m in pattern.finditer(line)
    ]

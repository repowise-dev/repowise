"""Which source lines start inside a string or a comment.

A lexer-lite over whole files, used to keep definition-shaped text inside
docstrings, template literals and block comments out of ``withheld_symbols``.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

# Anything that could open a string or a comment. A line with none of these
# cannot change the scanner's state, so it skips the character walk.
_QUOTEISH_RE = re.compile(r"""["'`#]|/[*/]""")


def _skip_quoted(raw: str, i: int) -> int:
    """Index just past the single- or double-quoted run starting at ``i``."""
    quote, i = raw[i], i + 1
    while i < len(raw):
        if raw[i] == "\\":
            i += 2
            continue
        if raw[i] == quote:
            return i + 1
        i += 1
    return i


# Extensions where a backtick opens a string: Go raw strings and JS/TS template
# literals. Elsewhere a backtick is punctuation (markdown fences in doc comments
# and heredocs), and a phantom frame there can re-close later, balanced, where
# the containment cannot see it.
_BACKTICK_STRING_SUFFIXES = frozenset(
    {".go", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
)

# Characters after which a ``/`` starts a regex rather than a division. Excludes
# ``)``, ``]`` and alphanumerics, where a division's left operand ends.
_REGEX_CAN_START_AFTER = frozenset("(,=:[!&|?{};+-*%~^<>\n")

# ...and the keywords a regex can follow, which would otherwise read as a
# division's left operand (``return /[`]/.test(x)``).
_REGEX_CAN_START_AFTER_WORD = frozenset(
    {
        "return", "case", "typeof", "yield", "await", "throw", "in", "of",
        "new", "delete", "instanceof", "do", "else", "void",
    }
)


def _regex_position(raw: str, i: int, prev: str) -> bool:
    """Whether the ``/`` at ``i`` starts a regex rather than a division."""
    if prev in _REGEX_CAN_START_AFTER:
        return True
    if not (prev.isalnum() or prev == "_"):
        return False
    word = ""
    j = i - 1
    while j >= 0 and raw[j].isspace():
        j -= 1
    while j >= 0 and (raw[j].isalnum() or raw[j] == "_"):
        word = raw[j] + word
        j -= 1
    return word in _REGEX_CAN_START_AFTER_WORD


def _skip_regex(raw: str, i: int) -> int:
    """Index past a regex literal starting at ``i``, or ``i`` if it is not one.

    A backtick inside a regex would otherwise open a phantom template literal
    that a later backtick closes, leaving a balanced stack the containment in
    ``_string_masked_lines`` cannot see while the mask eats the lines between.

    Regex literals cannot span lines, so a run with no closing ``/`` on the same
    line is not one and is left alone.
    """
    j = i + 1
    in_class = False
    while j < len(raw):
        c = raw[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            return j + 1
        j += 1
    return i


class _StringWalk:
    """The state ``_walk_string_state`` carries from one line to the next.

    Each ``_scan_*`` method owns one lexical state. It consumes characters until
    the state changes or the line ends and returns the index to resume at, so
    the per-line loop only dispatches on a transition, never per character.
    """

    __slots__ = ("backticks", "comments", "delim", "in_block", "open_line", "stack", "strings")

    def __init__(self, backticks: bool) -> None:
        self.backticks = backticks
        self.strings: set[int] = set()
        self.comments: set[int] = set()
        self.delim: str | None = None
        self.in_block = False
        # Where the open ``delim`` run or ``/* */`` block started; the two are
        # mutually exclusive, so one variable serves both.
        self.open_line = 0
        self.stack: list[int | None] = []

    def walk_line(self, n: int, raw: str) -> None:
        stack = self.stack
        # The three states are mutually exclusive: frames open only at code level.
        if self.delim is not None or (stack and stack[-1] is None):
            self.strings.add(n)
        elif self.in_block:
            self.comments.add(n)
        elif not stack and not _QUOTEISH_RE.search(raw):
            # Nothing here can change state; skipping keeps the scan cheap.
            return
        i = 0
        # Last non-space character seen at CODE level, for the regex test below.
        prev = "\n"
        while i < len(raw):
            if stack:
                if stack[-1] is None:
                    i = self._scan_template_text(raw, i)
                else:
                    i, prev = self._scan_interpolation(raw, i, prev)
            elif self.delim is not None:
                i = self._scan_to_close(raw, i, self.delim)
            elif self.in_block:
                i = self._scan_to_close(raw, i, "*/")
            else:
                i, prev = self._scan_code(raw, i, prev, n)

    def _scan_to_close(self, raw: str, i: int, closer: str) -> int:
        """Inside a ``delim`` run or a ``/* */`` block: find its terminator."""
        j = raw.find(closer, i)
        if j == -1:
            return len(raw)
        self.delim, self.in_block, self.open_line = None, False, 0
        return j + len(closer)

    def _scan_template_text(self, raw: str, i: int) -> int:
        """Inside the string part of a backtick literal."""
        stack = self.stack
        while i < len(raw):
            c = raw[i]
            if c == "\\":
                # An escaped backtick does not close the literal; misreading it
                # inverts parity with a balanced stack the containment misses.
                i += 2
            elif raw.startswith("${", i):
                stack.append(1)
                return i + 2
            elif c == "`":
                stack.pop()
                return i + 1
            else:
                i += 1
        return i

    def _scan_interpolation(self, raw: str, i: int, prev: str) -> tuple[int, str]:
        """Inside a ``${...}``: code, until its braces balance or a backtick nests.

        Ordinary code tokens apply here too, including the regex probe
        (``${s.replace(/[`]/g, "")}``).
        """
        stack = self.stack
        while i < len(raw):
            c = raw[i]
            if c == "{":
                stack[-1] += 1
            elif c == "}":
                stack[-1] -= 1
                if stack[-1] <= 0:
                    stack.pop()
                    return i + 1, c
            elif c == "`":
                stack.append(None)
                return i + 1, c
            elif raw.startswith("//", i):
                return len(raw), prev
            elif c == "/" and _regex_position(raw, i, prev):
                j = _skip_regex(raw, i)
                if j > i:
                    i, prev = j, "/"
                    continue
            elif c in ('"', "'"):
                i = _skip_quoted(raw, i)
                prev = '"'
                continue
            if not c.isspace():
                prev = c
            i += 1
        return i, prev

    def _scan_code(self, raw: str, i: int, prev: str, n: int) -> tuple[int, str]:
        """At code level: skip ordinary strings and regexes until a frame opens."""
        backticks = self.backticks
        while i < len(raw):
            if raw.startswith('"""', i) or raw.startswith("'''", i):
                self.delim, self.open_line = raw[i : i + 3], n
                return i + 3, prev
            c = raw[i]
            if backticks and c == "`":
                self.stack.append(None)
                return i + 1, prev
            if raw.startswith("/*", i):
                self.in_block, self.open_line = True, n
                return i + 2, prev
            if c == "#" or raw.startswith("//", i):
                return len(raw), prev
            if c == "/" and _regex_position(raw, i, prev):
                j = _skip_regex(raw, i)
                if j > i:
                    i, prev = j, "/"
                    continue
            if c in ('"', "'"):
                i = _skip_quoted(raw, i)
                prev = '"'
                continue
            if not c.isspace():
                prev = c
            i += 1
        return i, prev

    def result(self) -> tuple[set[int], set[int], bool]:
        # A run still open at EOF means the walk lost track (usually another
        # language's delimiter inside an untracked string). Discard it: a
        # spurious name in a list beats hiding every definition below.
        strings, comments = self.strings, self.comments
        if self.delim is not None:
            strings = {n for n in strings if n < self.open_line}
        elif self.in_block:
            comments = {n for n in comments if n < self.open_line}
        return strings, comments, bool(self.stack)


def _walk_string_state(
    lines: tuple[str, ...], *, backticks: bool
) -> tuple[set[int], set[int], bool]:
    """(lines starting inside a string, inside a block comment, literal left open).

    The sets are kept apart: a string body proves the enclosing expression is
    still open, a comment between two declarations proves nothing.

    ``stack`` models template-literal nesting: a ``None`` frame is the string
    part of a backtick literal, an ``int`` frame the brace depth inside a
    ``${...}`` interpolation. An interpolation holds code, so its lines are not
    masked and a backtick there opens a nested literal. A flat counter would
    read that backtick as closing the outer literal, then apply code rules
    (``#``, ``//``) to string content. Escaped backticks and regexes are the
    other triggers of that shape, and worse, since they leave the stack balanced.
    """
    walk = _StringWalk(backticks)
    for n, raw in enumerate(lines, 1):
        walk.walk_line(n, raw)
    return walk.result()


def _has_backtick_strings(file_path: str) -> bool:
    """Whether a backtick opens a string in this file's language."""
    dot = file_path.rfind(".")
    return dot != -1 and file_path[dot:].lower() in _BACKTICK_STRING_SUFFIXES


class _Masked(NamedTuple):
    """1-based line numbers, split by what is hiding them.

    ``all`` is precomputed: every caller wants it, and the walk is cached.
    """

    strings: frozenset[int]
    comments: frozenset[int]
    all: frozenset[int]


@lru_cache(maxsize=8)
def _string_masked_lines(lines: tuple[str, ...], backticks: bool = True) -> _Masked:
    """1-based line numbers that START inside a multi-line string or comment.

    Without this, ``def``/``class`` examples inside docstrings become symbol ids
    that resolve to nothing.

    Deliberately a lexer-lite: Python triple quotes, backtick template literals
    and Go raw strings, C-style ``/* */`` blocks; stops at ``#`` / ``//``. Known
    ceilings:

    * C# verbatim and Rust raw strings are not tracked: rare, and their
      contents seldom look like definitions.
    * Inside ``${...}``, ``/* */`` and ``#`` are not handled, so a ``}`` in a
      comment there can close the frame early; such cases end unbalanced and
      hit the fallback below.
    * Markdown fences and inline code spans are masked too (a ``def`` in a
      fence is not a definition).

    Cached on the line tuple: a per-character walk that the homonym path would
    otherwise repeat per truncated body. The fallback bounds it at two walks.
    """
    strings, comments, template_left_open = _walk_string_state(
        lines, backticks=backticks
    )
    if template_left_open:
        # A literal never closed, which would silently mask to EOF. Re-walk
        # without backticks: under-masking costs a spurious name, not a real one.
        strings, comments, _ = _walk_string_state(lines, backticks=False)
    return _Masked(
        frozenset(strings), frozenset(comments), frozenset(strings | comments)
    )

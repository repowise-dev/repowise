"""Pick one arm of C/C++ ``#if`` groups whose arms do not balance their braces.

tree-sitter reads every arm of ``#if``/``#else``/``#endif`` as live code and
needs each arm to close the braces it opens::

    int f(int a) {
    #ifdef X
      if (a) {          // both arms open a brace that only the shared
    #else               // ``}`` after ``#endif`` closes
      if (a) {
    #endif
      }
      return 2;
    }

Seen that way the function body never closes, the parse is one root ``ERROR``
node, and the function loses its symbol, its calls, its health score and its
prototype pairing. A compiler only ever sees one arm, so this module does the
same: it keeps one arm of each such group, blanks the directive lines and the
other arms, and leaves every byte offset and line number where it was.

Only a group with a second arm to choose from and an arm whose braces do not
net to zero is touched. A lone ``#ifdef __cplusplus`` / ``extern "C" {`` /
``#endif`` is unbalanced by design, is closed by a later lone group, and parses
as it always did. A group whose arms each balance parses as it always did too,
and so does a ``#if 0`` block that balances.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass

# A group needs a second arm to choose between, so a file without an
# ``#else``/``#elif`` line (``#elifdef`` included) skips the scan entirely.
_ARM_SWITCH_RE = re.compile(rb"^[ \t]*#[ \t]*el(?:se|if)", re.MULTILINE)

# One preprocessor logical line: continuation lines, a complete ``/* */``
# comment and string literals stay inside it, so a brace in ``#define X {`` or
# ``#error "{"`` is never counted. It stops before a ``//`` or an open ``/*``.
_DIRECTIVE = (
    rb"^[ \t]*#"
    rb"(?:[^\\\r\n/\"]|\\\r?\n|\\|/\*(?s:.*?)\*/|/(?![/*])|\"(?:[^\"\\\r\n]|\\.)*\"?)*"
)

# Everything that can hide a brace or fake a directive, then the braces.
# A character literal needs a non-word character before it, so the C++14 digit
# separator in ``1'000'000`` does not open one; an unterminated string or
# character literal ends at its line, so one stray quote cannot swallow the file.
_TOKEN_RE = re.compile(
    rb"(?P<dir>" + _DIRECTIVE + rb")"
    rb"|(?P<skip>//(?:[^\\\r\n]|\\\r?\n|\\)*"
    rb"|/\*(?s:.*?)(?:\*/|\Z)"
    rb"|(?<!\w)(?:u8|u|U|L)?R\"(?P<delim>[^()\\ \t\v\f\r\n]{0,16})\((?s:.*?)\)(?P=delim)\""
    rb"|\"(?:[^\"\\\r\n]|\\\r?\n|\\.)*\"?"
    rb"|(?<!\w)(?:u8|u|U|L)?'(?:[^'\\\r\n]|\\.)*'?)"
    rb"|(?P<brace>[{}])",
    re.MULTILINE,
)

_DIRECTIVE_NAME_RE = re.compile(rb"[ \t]*#[ \t]*([A-Za-z_]*)")
# ``#if 0`` and ``#elif 0``: an arm that is dead by construction, never kept
# while a live one is on offer.
_DEAD_ARM_RE = re.compile(rb"[ \t]*#[ \t]*(?:el)?if[ \t]*\(?[ \t]*0[ \t]*\)?[ \t]*\Z")
_OPENERS = frozenset({b"if", b"ifdef", b"ifndef"})
_SWITCHERS = frozenset({b"else", b"elif", b"elifdef", b"elifndef"})
_NOT_NEWLINE_RE = re.compile(rb"[^\r\n]")


@dataclass
class _Arm:
    head: bytes  # the directive line that opens the arm
    body_start: int
    body_end: int = 0
    delta: int = 0  # braces opened minus closed, nested groups included


@dataclass
class _Group:
    arms: list[_Arm]
    directives: list[tuple[int, int]]  # spans of the group's own #if/#else/#endif lines


class _GroupScan:
    """Walks the directive and brace events of a file in order.

    A group is settled at its ``#endif``, innermost first, so an outer arm's
    balance already counts only the arm its nested groups keep.
    """

    def __init__(self) -> None:
        self.stack: list[_Group] = []
        self.dir_starts: list[int] = []
        self.dir_ends: list[int] = []
        self.blank: list[tuple[int, int]] = []

    def brace(self, delta: int) -> None:
        if self.stack:
            self.stack[-1].arms[-1].delta += delta

    def directive(self, text: bytes, start: int, end: int) -> None:
        self.dir_starts.append(start)
        self.dir_ends.append(end)
        name = _DIRECTIVE_NAME_RE.match(text).group(1)  # type: ignore[union-attr]
        if name in _OPENERS:
            self.stack.append(_Group([_Arm(text, end)], [(start, end)]))
        elif name in _SWITCHERS and self.stack:
            group = self.stack[-1]
            group.arms[-1].body_end = start
            group.arms.append(_Arm(text, end))
            group.directives.append((start, end))
        elif name == b"endif" and self.stack:
            group = self.stack.pop()
            group.arms[-1].body_end = start
            group.directives.append((start, end))
            self._settle(group)

    def _settle(self, group: _Group) -> None:
        arms = group.arms
        if len(arms) > 1 and any(arm.delta for arm in arms):
            keep = next((arm for arm in arms if not _DEAD_ARM_RE.match(arm.head)), arms[0])
            self._drop_all_but(group, keep)
            contribution = keep.delta
        else:
            contribution = arms[0].delta
        if self.stack:
            self.stack[-1].arms[-1].delta += contribution

    def _drop_all_but(self, group: _Group, keep: _Arm) -> None:
        self.blank.extend(group.directives)
        for arm in group.arms:
            if arm is not keep:
                self._blank_body(arm.body_start, arm.body_end)

    def _blank_body(self, lo: int, hi: int) -> None:
        """Blank ``[lo, hi)`` except the preprocessor lines inside it.

        An ``#include`` or ``#define`` in a dropped arm still reaches the import
        and macro facts; the code around it, which is what broke the parse, goes.
        """
        pos = lo
        i = bisect_left(self.dir_starts, lo)
        while i < len(self.dir_starts) and self.dir_starts[i] < hi:
            if self.dir_starts[i] > pos:
                self.blank.append((pos, self.dir_starts[i]))
            pos = self.dir_ends[i]
            i += 1
        if pos < hi:
            self.blank.append((pos, hi))


def blank_unbalanced_preproc_arms(source: bytes) -> bytes:
    """Keep the first live arm of each brace-unbalanced ``#if`` group, blank the rest.

    Returns *source* itself when nothing needs blanking. Blanked bytes become
    spaces and every line break stays, so offsets and line numbers outside the
    dropped text are unchanged.
    """
    if not _ARM_SWITCH_RE.search(source):
        return source
    scan = _GroupScan()
    for match in _TOKEN_RE.finditer(source):
        brace = match["brace"]
        if brace is not None:
            scan.brace(1 if brace == b"{" else -1)
        elif match["dir"] is not None:
            scan.directive(match["dir"], *match.span("dir"))
    if not scan.blank:
        return source
    out = bytearray(source)
    for lo, hi in scan.blank:
        out[lo:hi] = _NOT_NEWLINE_RE.sub(b" ", out[lo:hi])
    return bytes(out)

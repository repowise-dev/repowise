"""Pick one arm of C/C++/C# ``#if`` groups whose arms do not balance their braces.

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
net to zero is touched, plus, in C++, a one-arm group that splits one
construct: its code does not end a declaration or statement (``;``, ``{``,
``}`` or a label's ``:``). There the directive sits inside a class head
(``class`` / ``#ifndef X`` / ``EXPORT alignas(16)`` / ``#endif`` / ``Body``), a
constructor's initializer list, or between an attribute and its member, and
the grammar, which places a directive only between whole declarations, loses
the enclosing class; only the directive lines go. An arm ending on a macro
alone on its line closes what it opened as far as a scan can tell (an include
guard ending in ``NAMESPACE_END``), and a split group with an ``#else`` stays,
since keeping one arm could drop the live one. A lone
``#ifdef __cplusplus`` / ``extern "C" {`` / ``#endif`` is unbalanced by
design, is closed by a later lone group, and parses as it always did. A group
whose arms each balance parses as it always did too,
and so does a ``#if 0`` block that balances.

C# differs in two ways. Its grammar reads ``#if``/``#elif``/``#else``/``#endif``
as a structural node allowed only between whole statements, members and
declarations, so one inside a ``switch`` block, before an ``else``, or between a
property and its initializer is an ``ERROR`` even when every arm balances. A C#
directive is always a whole line and never continues onto the next one, so
:func:`blank_csharp_preproc_arms` blanks every conditional directive line,
drops every ``#if false`` arm (once exposed, its code would read as live), and
still keeps one arm only of a group whose arms do not balance. ``#region``,
``#pragma``, ``#nullable`` and the other directives are extras the grammar
accepts anywhere, and stay. C# also has verbatim (``@"..."``) and raw
(three or more quotes) strings that span lines, and a ``//`` comment that a
trailing backslash does not continue, so it scans with its own token pattern.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass

# A group needs a second arm to choose between, so a file without an
# ``#else``/``#elif`` line (``#elifdef`` included) skips the scan entirely.
_ARM_SWITCH_RE = re.compile(rb"^[ \t]*#[ \t]*el(?:se|if)", re.MULTILINE)
# A one-arm group can split a construct, so that scan needs only an ``#if``.
_GROUP_OPEN_RE = re.compile(rb"^[ \t]*#[ \t]*if", re.MULTILINE)
# The last code byte of an arm that leaves no construct open; a string may be
# one piece of a concatenation the grammar already reads across directives.
_CONSTRUCT_ENDS = frozenset(b';{}:"')
_MACRO_ONLY_RE = re.compile(rb"[ \t]*[A-Z][A-Z0-9_]*(?:\(.*\))?")

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

# A C# line starts at a line break or after the byte-order mark editors write.
_CSHARP_LINE_START = rb"(?:^|(?<=\A\xef\xbb\xbf))[ \t]*#"
_CSHARP_IF_RE = re.compile(_CSHARP_LINE_START + rb"[ \t]*if\b", re.MULTILINE)

# The C# tokens: a directive is its whole line, verbatim and raw strings may
# span lines, and ``//`` ends at the line break whatever comes before it.
_CSHARP_TOKEN_RE = re.compile(
    rb"(?P<dir>" + _CSHARP_LINE_START + rb"[^\r\n]*)"
    rb"|(?P<skip>//[^\r\n]*"
    rb"|/\*(?s:.*?)(?:\*/|\Z)"
    rb"|(?P<quotes>\"{3,})(?s:.*?)(?:(?P=quotes)|\Z)"
    rb"|\$*@\$*\"(?:[^\"]|\"\")*\"?"
    rb"|\"(?:[^\"\\\r\n]|\\.)*\"?"
    rb"|'(?:[^'\\\r\n]|\\.)*'?)"
    rb"|(?P<brace>[{}])",
    re.MULTILINE,
)

_DIRECTIVE_NAME_RE = re.compile(rb"[ \t]*#[ \t]*([A-Za-z_]*)")
# ``#if 0`` and ``#elif 0``: an arm that is dead by construction, never kept
# while a live one is on offer.
_DEAD_ARM_RE = re.compile(rb"[ \t]*#[ \t]*(?:el)?if[ \t]*\(?[ \t]*0[ \t]*\)?[ \t]*\Z")
# C#'s ``#if false`` never compiles, so its arm goes even without a rival.
_CSHARP_DEAD_ARM_RE = re.compile(
    rb"[ \t]*#[ \t]*(?:el)?if[ \t]*\(?[ \t]*false[ \t]*\)?[ \t]*(?://[^\r\n]*)?\Z"
)
_CONDITIONALS = frozenset({b"if", b"elif", b"else", b"endif"})
_OPENERS = frozenset({b"if", b"ifdef", b"ifndef"})
_SWITCHERS = frozenset({b"else", b"elif", b"elifdef", b"elifndef"})
_NOT_NEWLINE_RE = re.compile(rb"[^\r\n]")


@dataclass
class _Arm:
    head: bytes  # the directive line that opens the arm
    body_start: int
    body_end: int = 0
    delta: int = 0  # braces opened minus closed, nested groups included
    code_mark: int = 0  # the scan's code count when the arm opened
    splits: bool = False  # its code ends inside a construct


@dataclass
class _Group:
    arms: list[_Arm]
    directives: list[tuple[int, int]]  # spans of the group's own #if/#else/#endif lines


class _GroupScan:
    """Walks the directive and brace events of a file in order.

    A group is settled at its ``#endif``, innermost first, so an outer arm's
    balance already counts only the arm its nested groups keep.
    """

    def __init__(
        self, source: bytes, *, csharp: bool = False, split_constructs: bool = False
    ) -> None:
        self.source = source
        self.csharp = csharp
        self.split_constructs = split_constructs
        self.code_count = 0  # stretches of code seen, comments and directives skipped
        self.last_code = 0  # offset of the last byte of the latest one
        self.pos = 0  # end of the last token fed
        self.stack: list[_Group] = []
        self.dir_starts: list[int] = []
        self.dir_ends: list[int] = []
        self.blank: list[tuple[int, int]] = []

    def token(self, match: re.Match[bytes]) -> None:
        """Feed one ``_TOKEN_RE`` match, and the plain code before it."""
        if self.split_constructs:
            gap = self.source[self.pos : match.start()].rstrip()
            if gap:
                self.code(self.pos + len(gap) - 1)
            self.pos = match.end()
        brace = match["brace"]
        if brace is not None:
            self.code(match.start())
            self.brace(1 if brace == b"{" else -1)
        elif match["dir"] is not None:
            self.directive(match["dir"], *match.span("dir"))
        elif not match["skip"].startswith(b"/"):
            self.code(match.end() - 1)  # a string or character literal

    def code(self, at: int) -> None:
        self.code_count += 1
        self.last_code = at

    def brace(self, delta: int) -> None:
        if self.stack:
            self.stack[-1].arms[-1].delta += delta

    def directive(self, text: bytes, start: int, end: int) -> None:
        self.dir_starts.append(start)
        self.dir_ends.append(end)
        name = _DIRECTIVE_NAME_RE.match(text).group(1)  # type: ignore[union-attr]
        if self.csharp and name in _CONDITIONALS:
            self.blank.append((start, end))
        if name in _OPENERS:
            self.stack.append(_Group([self._open_arm(text, end)], [(start, end)]))
        elif name in _SWITCHERS and self.stack:
            group = self.stack[-1]
            self._close_arm(group.arms[-1], start)
            group.arms.append(self._open_arm(text, end))
            group.directives.append((start, end))
        elif name == b"endif" and self.stack:
            group = self.stack.pop()
            self._close_arm(group.arms[-1], start)
            group.directives.append((start, end))
            self._settle(group)

    def _open_arm(self, head: bytes, body_start: int) -> _Arm:
        return _Arm(head, body_start, code_mark=self.code_count)

    def _close_arm(self, arm: _Arm, body_end: int) -> None:
        arm.body_end = body_end
        if self.split_constructs and self.code_count != arm.code_mark:
            at = self.last_code
            line = self.source[self.source.rfind(b"\n", 0, at) + 1 : at + 1]
            open_end = self.source[at] not in _CONSTRUCT_ENDS
            arm.splits = open_end and _MACRO_ONLY_RE.fullmatch(line) is None

    def _settle(self, group: _Group) -> None:
        arms = self._drop_csharp_dead_arms(group.arms) if self.csharp else group.arms
        unbalanced = len(arms) > 1 and any(arm.delta for arm in arms)
        # A split with a rival arm stays: keeping one arm could drop the live one.
        if unbalanced or (len(arms) == 1 and arms[0].splits):
            keep = next((arm for arm in arms if not _DEAD_ARM_RE.match(arm.head)), arms[0])
            self._drop_all_but(group, keep)
            contribution = keep.delta
        else:
            contribution = arms[0].delta if arms else 0
        if self.stack:
            self.stack[-1].arms[-1].delta += contribution

    def _drop_csharp_dead_arms(self, arms: list[_Arm]) -> list[_Arm]:
        live = []
        for arm in arms:
            if _CSHARP_DEAD_ARM_RE.match(arm.head):
                self._blank_body(arm.body_start, arm.body_end)
            else:
                live.append(arm)
        return live

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


def blank_unbalanced_preproc_arms(source: bytes, *, split_constructs: bool = False) -> bytes:
    """Keep the first live arm of each brace-unbalanced ``#if`` group, blank the rest.

    With *split_constructs* (C++), a group with an arm that splits a construct
    is settled the same way. Returns *source* itself when nothing needs
    blanking. Blanked bytes become spaces and every line break stays, so
    offsets and line numbers outside the dropped text are unchanged.
    """
    if not (_GROUP_OPEN_RE if split_constructs else _ARM_SWITCH_RE).search(source):
        return source
    scan = _GroupScan(source, split_constructs=split_constructs)
    return _blank_arms(source, _TOKEN_RE, scan)


def blank_csharp_preproc_arms(source: bytes) -> bytes:
    """Blank every C# ``#if``/``#elif``/``#else``/``#endif`` line, every
    ``#if false`` arm, and the losing arms of each brace-unbalanced group, under
    the same offset contract."""
    if not _CSHARP_IF_RE.search(source):
        return source
    return _blank_arms(source, _CSHARP_TOKEN_RE, _GroupScan(source, csharp=True))


def _blank_arms(source: bytes, token_re: re.Pattern[bytes], scan: _GroupScan) -> bytes:
    for match in token_re.finditer(source):
        scan.token(match)
    if not scan.blank:
        return source
    out = bytearray(source)
    for lo, hi in scan.blank:
        out[lo:hi] = _NOT_NEWLINE_RE.sub(b" ", out[lo:hi])
    return bytes(out)

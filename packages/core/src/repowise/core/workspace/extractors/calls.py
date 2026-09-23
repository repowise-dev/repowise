"""Call sites as a table-driven dialect reads them.

A recogniser (topic, socket, HTTP, data) is a table of call shapes: a *head*
regex ending at the call's opening parenthesis, the extensions it applies to,
and the substrings one of which the file must contain (the library's import)
before the head is tried at all. :func:`call_sites` walks one file through such a
table and yields each call's argument list with the file's
:class:`FileStrings`, which reads the file's constants once, on first use.
:func:`call_chain` reads the calls chained onto one (``X::dispatch(...)
->onQueue('q')``, ``Echo.private(c).listen(e)``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .strings import (
    call_arguments,
    match_paren,
    resolve_argument,
    resolve_string,
    string_constants,
    syntax_for_suffix,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from .base import ScanContext
    from .strings import Arg, StringSyntax


class CallShape(Protocol):
    """What :func:`call_sites` reads from a table row."""

    head: re.Pattern[str]
    extensions: frozenset[str]
    requires: tuple[str, ...]


@dataclass(frozen=True)
class Call:
    """A table row for :func:`call_sites` with nothing but the shape."""

    head: re.Pattern[str]
    extensions: frozenset[str]
    requires: tuple[str, ...] = ()


class FileStrings:
    """One file's string syntax, and its constants, read the first time they are needed."""

    __slots__ = ("_code", "_constants", "content", "syntax")

    def __init__(self, content: str, syntax: StringSyntax, code: str | None = None) -> None:
        self.content = content
        self.syntax = syntax
        self._code = code
        self._constants: dict[str, str] | None = None

    @property
    def constants(self) -> dict[str, str]:
        if self._constants is None:
            self._constants = string_constants(self.content, self.syntax, self._code)
        return self._constants

    def resolve(
        self, args: list[str], arg: Arg, normalize: Callable[[str], str | None] | None = None
    ) -> tuple[list[str], bool]:
        """:func:`..strings.resolve_argument` against this file's constants."""
        return resolve_argument(args, arg, self.syntax, self.constants, normalize)

    def text(self, expr: str) -> str | None:
        """:func:`..strings.resolve_string` for *expr*, holes allowed.

        The constants are read only when the expression alone leaves a hole
        or does not resolve, so a plain literal never pays for them.
        """
        text = resolve_string(expr, self.syntax)
        if text is None or "${" in text:
            text = resolve_string(expr, self.syntax, self.constants)
        return text


def file_strings(ctx: ScanContext) -> FileStrings | None:
    """The :class:`FileStrings` for *ctx*, or ``None`` for a language with no string syntax."""
    syntax = syntax_for_suffix(ctx.suffix)
    return FileStrings(ctx.content, syntax) if syntax is not None else None


def call_sites(
    ctx: ScanContext,
    calls: Iterable[CallShape],
    strings: FileStrings | None = None,
    *,
    empty: bool = False,
) -> Iterator[tuple[CallShape, list[str], re.Match[str], FileStrings]]:
    """Each call in *ctx* one of *calls* recognises, with its argument list.

    A call with no arguments is skipped unless *empty* is set: for a decorator
    such as ``@Get()`` the missing argument is the answer. Pass *strings* when
    the caller already holds the file's, so its constants are read once.
    """
    present: dict[tuple[str, ...], bool] = {}  # rows of one library share a gate
    for call in calls:
        if ctx.suffix not in call.extensions:
            continue
        if call.requires:
            if call.requires not in present:
                present[call.requires] = any(r in ctx.content for r in call.requires)
            if not present[call.requires]:
                continue
        for m in call.head.finditer(ctx.content):
            args = call_arguments(ctx.content, m.end() - 1)
            if args is None or not (args or empty):
                continue
            if strings is None:
                strings = file_strings(ctx)
                if strings is None:
                    return
            yield call, args, m, strings


# One link of a method chain: `->name(` (PHP) or `.name(` / `?.name(` (JS).
_CHAIN_LINK_RE = re.compile(r"\s*(?:->|\?->|\??\.)\s*(?P<name>[A-Za-z_]\w*)\s*\(")


@dataclass(frozen=True)
class ChainLink:
    """One call chained onto another: its method name, arguments and offset."""

    name: str
    args: list[str]
    offset: int


def call_chain(content: str, close: int) -> Iterator[ChainLink]:
    """The calls chained onto the one whose ``)`` is at *close*, in order."""
    pos = close + 1
    while True:
        m = _CHAIN_LINK_RE.match(content, pos)
        if m is None:
            return
        end = match_paren(content, m.end() - 1)
        if end < 0:
            return
        args = call_arguments(content, m.end() - 1, end) or []
        yield ChainLink(m.group("name"), args, m.start("name"))
        pos = end + 1


def receiver_at(content: str, start: int, qualifiers: re.Pattern[str] | None = None) -> bool:
    """Whether a name matched at *start* stands alone rather than ending another name.

    Read here rather than by a lookbehind, which would cost a head regex its
    literal start. A ``x.name`` member counts only when *qualifiers* matches
    the text before the dot (``this.``, ``window.``).
    """
    if start == 0:
        return True
    before = content[start - 1]
    if before.isalnum() or before in "_$":
        return False
    head = content[max(0, start - 16) : start]
    if not head.rstrip().endswith("."):
        return True
    return qualifiers is not None and qualifiers.search(head) is not None


# Whitespace and comments between decorators and the member they decorate.
_GAP_RE = re.compile(r"(?:\s+|//[^\n]*|/\*.*?\*/)*", re.DOTALL)
_DECORATOR_NAME_RE = re.compile(r"@[\w$.]+")
_MEMBER_RE = re.compile(
    r"(?:(?:export|default|declare|abstract|public|private|protected|static|async|readonly"
    r"|override)\s+)*(?:(?P<cls>class)\s+)?(?P<name>[A-Za-z_$][\w$]*)"
)


def decorated_member(content: str, pos: int) -> re.Match[str] | None:
    """The TypeScript class or method a decorator run continuing at *pos* decorates.

    Further decorators, their arguments and comments are stepped over. The
    match names the member (``name``) and says whether it is a class (``cls``).
    TypeScript only: Java annotation runs are read by ``framework_routes``.
    """
    while True:
        pos = _GAP_RE.match(content, pos).end()
        name = _DECORATOR_NAME_RE.match(content, pos)
        if name is None:
            return _MEMBER_RE.match(content, pos)
        pos = _GAP_RE.match(content, name.end()).end()
        if content.startswith("(", pos):
            close = match_paren(content, pos)
            if close < 0:
                return None
            pos = close + 1


__all__ = [
    "Call",
    "CallShape",
    "ChainLink",
    "FileStrings",
    "call_chain",
    "call_sites",
    "decorated_member",
    "file_strings",
    "receiver_at",
]

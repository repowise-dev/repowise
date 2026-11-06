"""Shared reading for route DSLs that nest paths in lambdas (Ktor, Javalin).

Both frameworks declare a route as a bare call that opens a block, and nest
prefixes the same way: ``route("/api") { get("/x") { } }`` in Ktor,
``path("/api", () -> { get("/x", h); })`` in Javalin's ``ApiBuilder``. The
prefix a route serves under is every enclosing prefix block, by lambda scope
rather than by line order, so a route after a closed block does not inherit it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..strings import match_paren
from .mounts import compose_prefix


@dataclass(frozen=True)
class PrefixScope:
    """A block that prefixes the routes inside it.

    *prefix* is ``None`` when the block's path could not be read: a route
    inside it is not served at the path written on it, so it is refused.
    """

    start: int
    end: int
    prefix: str | None


def trailing_block(content: str, offset: int) -> int:
    """Index of the ``{`` opening a block right after *offset*, or -1."""
    i, n = offset, len(content)
    while i < n and content[i] in " \t\r\n":
        i += 1
    return i if i < n and content[i] == "{" else -1


def block_end(content: str, open_idx: int) -> int:
    """Index of the ``}`` closing the block opened at *open_idx*, or -1."""
    return match_paren(content, open_idx, closer="}")


def is_call_site(content: str, offset: int) -> bool:
    """True when the name at *offset* is called rather than declared.

    A declaration names a type or ``fun`` before the name (``void get(``,
    ``String get(``, ``fun get(``); a call follows a statement boundary, a
    bracket, an operator, a lambda arrow or a line break.
    """
    i = offset - 1
    while i >= 0 and content[i] in " \t":
        i -= 1
    if i < 0:
        return True
    prev = content[i]
    if prev == ">":
        # `List<String> get(` declares; `() -> get(` calls, in an expression lambda.
        return content[i - 1 : i + 1] == "->"
    return not (prev.isalnum() or prev in "_]")


def enclosing_prefix(scopes: list[PrefixScope], offset: int) -> str | None:
    """The prefix every scope around *offset* composes to, outermost first.

    ``None`` when one of them could not be read.
    """
    prefix = ""
    for scope in sorted(scopes, key=lambda s: s.start):
        if scope.start < offset < scope.end:
            if scope.prefix is None:
                return None
            prefix = compose_prefix(prefix, scope.prefix)
    return prefix

"""Javalin HTTP provider dialect, Java and Kotlin.

Javalin registers a route in three shapes, all read here:

* a verb on the app, Express style: ``app.get("/orders/{id}", ctx -> ...)``
  (v5/v6), where ``app`` is bound to ``Javalin.create(...)`` or typed
  ``Javalin``, or chained straight off ``Javalin.create(...)``;
* a verb on the config's routes (v7): ``config.routes.get("/x", ctx -> ...)``;
* ``ApiBuilder``'s static calls, nested in ``path`` blocks:
  ``path("/api", () -> { get("/x", h); get(h); })``. A verb with no path
  serves the enclosing ``path``, by lambda scope.

A route needs a handler, which is what tells it from a lookup: a path and a
handler argument, or a path and a trailing Kotlin lambda. A dotted verb on any
other receiver (a map, the test client) is not a route, and the bare
``ApiBuilder`` calls are read only in a file that imports it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import HTTP_METHODS

from ..base import line_at
from ..calls import FileStrings, code_matches
from ..langs import JAVA, KOTLIN
from ..strings import JAVA_SYNTAX, KOTLIN_SYNTAX, call_arguments, match_paren
from .dialect import build_provider_contract
from .lambda_routes import (
    PrefixScope,
    block_end,
    enclosing_prefix,
    is_call_site,
    trailing_block,
)
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_JAVALIN = "io.javalin"
_API_BUILDER = "io.javalin.apibuilder"

_VERBS = r"get|post|put|patch|delete|head|options"

# `Javalin.create(` / `Javalin.create {`: the head of a chain of routes.
_CREATE_RE = re.compile(r"\bJavalin\s*\.\s*create\s*([({])")
# Names bound to a Javalin app: `app = Javalin.create`, `Javalin app`, `app: Javalin`.
_BINDING_RES = (
    re.compile(r"\b([A-Za-z_]\w*)\s*=\s*Javalin\s*\.\s*create\b"),
    re.compile(r"\bJavalin\s+([A-Za-z_]\w*)\b"),
    re.compile(r"\b([A-Za-z_]\w*)\s*:\s*Javalin\b"),
)
# `app.get(` / `config.routes.get(`.
_DOTTED_RE = re.compile(rf"(?<![\w.])([A-Za-z_]\w*)((?:\s*\.\s*routes)?)\s*\.\s*({_VERBS})\s*\(")
# A link of the chain after `Javalin.create(...)`: `.get(`, `.start(`.
_CHAIN_LINK_RE = re.compile(r"\s*\.\s*([A-Za-z_]\w*)\s*\(")
# ApiBuilder's bare calls: `path("/api", ...)`, `get("/x", h)`, `get(h)`, `get {`.
_BARE_RE = re.compile(rf"(?<![\w.])(path|{_VERBS})\s*([({{])")


def _is_handler(arg: str) -> bool:
    """A lambda or method reference, the handler a path-less verb takes."""
    return "->" in arg or "::" in arg


class _Reader:
    """The routes one file registers, by the offset of their verb."""

    def __init__(self, ctx: ScanContext) -> None:
        self.ctx = ctx
        self.content = ctx.content
        syntax = KOTLIN_SYNTAX if ctx.suffix in KOTLIN else JAVA_SYNTAX
        self.strings = FileStrings(self.content, syntax)
        self.routes: dict[int, tuple[str, str]] = {}

    def add(self, offset: int, verb: str, path: str) -> None:
        if verb.upper() in HTTP_METHODS and path:
            self.routes[offset] = (verb.upper(), path)

    def read(self, offset: int, verb: str, paren: int, scopes: list[PrefixScope] | None) -> int:
        """Record the route whose argument list opens at *paren*; return where it ends.

        *scopes* are the enclosing ``path`` blocks of an ``ApiBuilder`` call,
        ``None`` for a verb on the app.
        """
        content = self.content
        close = match_paren(content, paren)
        if close < 0:
            return -1
        args = call_arguments(content, paren, close) or []
        block = trailing_block(content, close + 1)
        path = self._own_path(args, block >= 0, scopes is not None)
        prefix = "" if scopes is None else enclosing_prefix(scopes, offset)
        if path is not None and prefix is not None:
            self.add(offset, verb, compose_prefix(prefix, path))
        return block_end(content, block) if block >= 0 else close

    def _own_path(self, args: list[str], has_block: bool, api_builder: bool) -> str | None:
        """The path a route names, ``""`` for a path-less handler, ``None`` for no route."""
        path = self.strings.text(args[0]) if args else None
        if path is not None:
            # An interpolated path, or a path with no handler (a lookup), is no route.
            has_handler = len(args) >= 2 or has_block
            return path if has_handler and "${" not in path else None
        if not api_builder:
            return None
        if (len(args) == 1 and _is_handler(args[0])) or (not args and has_block):
            return ""
        return None

    def read_app_verbs(self) -> None:
        """``app.get(...)`` on a bound app, and ``config.routes.get(...)``."""
        bindings = {m.group(1) for r in _BINDING_RES for m in r.finditer(self.content)}
        for m in code_matches(self.ctx, _DOTTED_RE):
            if m.group(2) or m.group(1) in bindings:
                self.read(m.start(3), m.group(3), m.end() - 1, None)

    def read_chains(self) -> None:
        """Verbs chained straight off ``Javalin.create(...)``."""
        for m in code_matches(self.ctx, _CREATE_RE):
            closer = "}" if m.group(1) == "{" else ")"
            self._follow_chain(match_paren(self.content, m.end() - 1, closer=closer))

    def _follow_chain(self, pos: int) -> None:
        content = self.content
        while pos >= 0:
            block = trailing_block(content, pos + 1)
            if block >= 0:
                pos = block_end(content, block)
                continue
            link = _CHAIN_LINK_RE.match(content, pos + 1)
            if link is None:
                return
            if re.fullmatch(_VERBS, link.group(1)):
                pos = self.read(link.start(1), link.group(1), link.end() - 1, None)
            else:
                pos = match_paren(content, link.end() - 1)

    def read_api_builder(self) -> None:
        """``ApiBuilder``'s bare calls, nested in ``path`` blocks."""
        if _API_BUILDER not in self.content:
            return
        scopes: list[PrefixScope] = []
        bare: list[tuple[int, str, int]] = []
        for m in code_matches(self.ctx, _BARE_RE):
            if not is_call_site(self.content, m.start()):
                continue
            if m.group(1) != "path":
                bare.append((m.start(), m.group(1), m.end() - 1))
            elif m.group(2) == "(":
                scope = self._path_scope(m.end() - 1)
                if scope is not None:
                    scopes.append(scope)
        for offset, verb, opener in bare:
            if offset not in self.routes:
                self._read_bare(offset, verb, opener, scopes)

    def _path_scope(self, paren: int) -> PrefixScope | None:
        """The scope of the ``path(...)`` call whose argument list opens at *paren*."""
        content = self.content
        close = match_paren(content, paren)
        if close < 0:
            return None
        args = call_arguments(content, paren, close) or []
        path = self.strings.text(args[0]) if args else None
        if path is not None and "${" in path:
            path = None
        block = trailing_block(content, close + 1)
        if block >= 0:
            return PrefixScope(block, block_end(content, block), path)
        return PrefixScope(paren, close, path)

    def _read_bare(self, offset: int, verb: str, opener: int, scopes: list[PrefixScope]) -> None:
        if self.content[opener] == "(":
            self.read(offset, verb, opener, scopes)
            return
        # `get { ctx -> }`: Kotlin's trailing handler, no path.
        prefix = enclosing_prefix(scopes, offset)
        if prefix:
            self.add(offset, verb, prefix)

    def contracts(self) -> list[Contract]:
        out: list[Contract] = []
        for offset in sorted(self.routes):
            method, path = self.routes[offset]
            c = build_provider_contract(
                self.ctx,
                method=method,
                path_raw=path,
                framework="javalin",
                line=line_at(self.content, offset),
            )
            if c is not None:
                out.append(c)
        return out


class JavalinDialect:
    name = "javalin"
    extensions = JAVA | KOTLIN
    requires = (_JAVALIN,)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        reader = _Reader(ctx)
        reader.read_app_verbs()
        reader.read_chains()
        reader.read_api_builder()
        return reader.contracts()

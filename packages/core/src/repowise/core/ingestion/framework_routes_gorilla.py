"""gorilla/mux route chains.

A gorilla route is a chain of matchers ending in a handler, in any order::

    r.Methods("GET").Path("/catalogue/{id}").Handler(h)
    r.Path("/x").Methods(http.MethodPost).HandlerFunc(f)
    r.PathPrefix("/static/").Handler(fs)
    api := r.PathPrefix("/api").Subrouter()

``r.HandleFunc("/x", h).Methods("GET")`` starts with a ``net/http``-shaped
registration, so :func:`.framework_routes.go_routes` reads it, trailing
``Methods`` included. A ``PathPrefix`` route is recorded at its prefix. Read only
in a file importing gorilla/mux.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from .framework_routes import (
    GroupMatch,
    RouteMatch,
    go_match_paren,
    go_methods,
)

_IMPORT = '"github.com/gorilla/mux'

# A chain opens on one of the matchers a route builder starts with. The
# receiver must start a word, so a link in the middle of a chain is not
# re-read as a chain of its own; an assignment before it binds a subrouter.
_CHAIN_HEAD_RE = re.compile(
    r"(?:(?P<var>\w+)\s*:?=\s*)?(?<!\w)(?P<receiver>\w+)\s*\.\s*"
    r"(?P<link>Methods|Path|PathPrefix|Name|Schemes|Headers|Queries)\s*\("
)
_LINK_RE = re.compile(r"\s*\.\s*(?P<link>\w+)\s*\(")
_LITERAL_RE = re.compile(r"""\s*(?P<q>["`])(?P<body>[^"`]*)(?P=q)\s*""")
_HANDLER_RE = re.compile(r"\s*(?P<handler>(?!(?:func|struct)\b)[A-Za-z_][\w.]*)\s*(?P<after>[,)({])")


def _links(content: str, paren: int) -> Iterator[tuple[str, int]]:
    """``(name, paren)`` of the call at *paren* and every call chained onto it."""
    link = content[content.rfind(".", 0, paren) + 1 : paren].strip()
    while True:
        yield link, paren
        close = go_match_paren(content, paren)
        if close < 0:
            return
        m = _LINK_RE.match(content, close + 1)
        if m is None:
            return
        link, paren = m.group("link"), m.end() - 1


def _literal(content: str, paren: int) -> str | None:
    """The single string-literal argument of the call at *paren*, else ``None``."""
    close = go_match_paren(content, paren)
    m = _LITERAL_RE.fullmatch(content, paren + 1, close) if close > 0 else None
    return m.group("body") if m else None


class _Chain:
    __slots__ = ("handled", "handler", "handler_call", "methods", "path_paren", "subrouter")

    def __init__(self, content: str, paren: int) -> None:
        self.methods: tuple[str, ...] | None = None
        self.path_paren: int | None = None
        self.handler: str | None = None
        self.handler_call = False
        self.handled = False  # the chain reaches a Handler/HandlerFunc link
        self.subrouter = False
        for link, at in _links(content, paren):
            if link == "Methods":
                self.methods = go_methods(content, at)
            elif link in ("Path", "PathPrefix"):
                self.path_paren = at
            elif link in ("Handler", "HandlerFunc"):
                m = _HANDLER_RE.match(content, at + 1)
                self.handler = m.group("handler") if m else None
                self.handler_call = bool(m) and m.group("after") == "("
                # Handler returns the *Route, so matchers may follow it.
                self.handled = True
            elif link == "Subrouter":
                self.subrouter = True
                return


def _chains(content: str) -> Iterator[tuple[re.Match[str], _Chain]]:
    if _IMPORT not in content:
        return
    for m in _CHAIN_HEAD_RE.finditer(content):
        yield m, _Chain(content, m.end() - 1)


def gorilla_routes(content: str) -> Iterator[RouteMatch]:
    """Route-builder chains ending in a handler, one row per verb.

    ``path`` is the ``Path``/``PathPrefix`` literal, or ``None`` with
    ``paren_offset`` at that call when its argument is an expression. A chain
    with no path matcher or no handler is not a route this can name. An unreadable
    ``Methods`` leaves the route verbless (``HANDLE``).
    """
    for m, chain in _chains(content):
        # No handler link: a route builder bound to a name for later.
        if not chain.handled or chain.path_paren is None:
            continue
        for verb in chain.methods or ("HANDLE",):
            yield RouteMatch(
                verb=verb,
                path=_literal(content, chain.path_paren),
                receiver=m.group("receiver"),
                handler=chain.handler,
                offset=m.start("receiver"),
                paren_offset=chain.path_paren,
                handler_call=chain.handler_call,
            )


def gorilla_groups(content: str) -> Iterator[GroupMatch]:
    """``sub := r.PathPrefix("/api").Subrouter()`` bindings.

    A subrouter with no ``PathPrefix`` (``r.Methods("GET").Subrouter()``) adds
    no prefix; one whose prefix is an expression is unreadable (``None``).
    """
    for m, chain in _chains(content):
        if not chain.subrouter or m.group("var") is None:
            continue
        prefix = "" if chain.path_paren is None else _literal(content, chain.path_paren)
        yield GroupMatch(var=m.group("var"), parent=m.group("receiver"), prefix=prefix)

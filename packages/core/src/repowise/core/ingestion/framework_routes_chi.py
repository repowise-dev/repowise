"""chi and fiber routes: title-case verbs and ``Route`` closures.

Both routers spell a verb ``r.Get("/x", h)``, which is also how a Go cache, a
config map or an HTTP client is called. So a call counts only in a file that
imports the router, on a receiver bound to one at that point of the file
(``chi.NewRouter()``, ``fiber.New()``, a router's ``Group``, a parameter or
field typed ``chi.Router``), with a path that starts with ``/``. Not read: a
router reached through a field typed in another file, a chain such as
``r.With(mw).Get(...)``, and a ``Mount`` prefix, so a sub-router mounted
elsewhere records its routes without the mount's prefix.

``r.Route("/api", func(r chi.Router) {...})`` serves the closure's routes under
``/api``; :func:`route_scopes` returns those blocks for the contract consumer to
stitch. Each framework is one row of :data:`_ROUTERS`.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterator
from dataclasses import dataclass

from .framework_routes import RouteMatch, go_match_paren, go_route_matches, go_route_pattern

# (import path, package name, router constructors, router types)
_ROUTERS: tuple[tuple[str, str, str, str], ...] = (
    ("github.com/go-chi/chi", "chi", "NewRouter|NewMux", "Router|Mux"),
    ("github.com/gofiber/fiber", "fiber", "New", "Router|App"),
)

_TITLE_VERBS = "Get|Post|Put|Delete|Patch|Head|Options|Connect|Trace|All"
_TITLE_ROUTE_RE = go_route_pattern(
    _TITLE_VERBS, r"""(?P<q>["`])(?P<path>/[^"`]*)(?P=q)(?!\s*\+)"""
)

# What a name is bound to, in file order: `r := chi.NewRouter()` assigns it,
# `func(r chi.Router)` or a struct field `mux *chi.Mux` declares its type.
_ASSIGN_RE = re.compile(r"(?<![\w.])(?P<var>\w+)\s*:?=(?!=)\s*(?P<rhs>[^\n]{0,120})")
_TYPED_RE = re.compile(r"(?<![\w.])(?P<var>\w+)\s+(?P<type>\*?[A-Za-z_][\w.]*)\s*(?=[,)\n;])")
_GROUP_CALL_RE = re.compile(r"(?P<parent>\w+)\s*\.\s*(?:Group|Route)\s*\(")


def _router_res(pkg: str, ctors: str, types: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    return (
        re.compile(rf"{pkg}\s*\.\s*(?:{ctors})\s*\("),
        re.compile(rf"\*?{pkg}\s*\.\s*(?:{types})"),
    )


_ROUTER_RES = tuple((path, *_router_res(pkg, c, t)) for path, pkg, c, t in _ROUTERS)


# A function's parameter list: `func name(`, `func (s *S) name(`, `func(`.
_FUNC_PARAMS_RE = re.compile(r"\bfunc\s*(?:\([^()]*\)\s*)?\w*\s*\(")


def _param_scopes(content: str) -> list[tuple[int, int, int]]:
    """``(params_start, params_end, body_end)`` of every function, so a parameter's
    binding ends with its body: a handler literal's ``r *http.Request`` does not
    rebind the router ``r`` it is registered on."""
    out: list[tuple[int, int, int]] = []
    for m in _FUNC_PARAMS_RE.finditer(content):
        close = go_match_paren(content, m.end() - 1)
        brace = content.find("{", close) if close > 0 else -1
        end = go_match_paren(content, brace) if brace > 0 else -1
        if end > 0:
            out.append((m.end(), close, end))
    return out


def _bindings(content: str) -> list[tuple[int, str, re.Match[str], bool, int | None]]:
    """``(offset, name, match, typed, scope_end)`` for every binding, in file order.

    *scope_end* is where a parameter's binding stops holding; ``None`` for an
    assignment or a struct field.
    """
    scopes = _param_scopes(content)
    found: list[tuple[int, str, re.Match[str], bool, int | None]] = [
        (m.start(), m.group("var"), m, False, None) for m in _ASSIGN_RE.finditer(content)
    ]
    for m in _TYPED_RE.finditer(content):
        at = m.start()
        end = next((e for s, c, e in scopes if s <= at < c), None)
        found.append((at, m.group("var"), m, True, end))
    return sorted(found, key=lambda b: b[0])


def _is_router(
    match: re.Match[str],
    typed: bool,
    imported: list[tuple[re.Pattern[str], re.Pattern[str]]],
    routers: dict[str, bool],
) -> bool:
    if typed:
        return any(t.fullmatch(match.group("type")) for _ctor, t in imported)
    rhs = match.group("rhs")
    if any(ctor.match(rhs) for ctor, _t in imported):
        return True
    group = _GROUP_CALL_RE.match(rhs)
    return group is not None and routers.get(group.group("parent"), False)


def _imported(content: str) -> list[tuple[re.Pattern[str], re.Pattern[str]]]:
    return [(ctor, typed) for path, ctor, typed in _ROUTER_RES if f'"{path}' in content]


class _RouterNames:
    """Which names hold a router, replayed through the file's bindings in order."""

    def __init__(self, content: str, imported: list[tuple[re.Pattern[str], re.Pattern[str]]]):
        self._bindings = _bindings(content)
        self._imported = imported
        self._next = 0
        self._routers: dict[str, bool] = {}
        # A parameter's binding is undone at its body's end: (end, seq, name, prior).
        self._restores: list[tuple[int, int, str, bool]] = []

    def _step(self, offset: int) -> bool:
        """Apply the next restore or binding before *offset*; False when none is left."""
        nxt = self._bindings[self._next][0] if self._next < len(self._bindings) else offset
        if self._restores and self._restores[0][0] < min(nxt, offset):
            _end, _seq, name, prior = heapq.heappop(self._restores)
            self._routers[name] = prior
            return True
        if nxt >= offset:
            return False
        _at, name, m, typed, scope_end = self._bindings[self._next]
        if scope_end is not None:
            prior = self._routers.get(name, False)
            heapq.heappush(self._restores, (scope_end, self._next, name, prior))
        self._routers[name] = _is_router(m, typed, self._imported, self._routers)
        self._next += 1
        return True

    def is_router(self, name: str | None, offset: int) -> bool:
        """Whether *name* holds a router at *offset*; offsets must not decrease."""
        while self._step(offset):
            pass
        return self._routers.get(name or "", False)


def title_case_routes(content: str) -> Iterator[RouteMatch]:
    """chi / fiber ``router.Get("/path", handler)`` registrations in *content*.

    The receiver's binding is the nearest one before the call, so a name a
    later function rebinds (``func b(r *Client)``) is not read as the router
    an earlier one built.
    """
    imported = _imported(content)
    if not imported:
        return
    names = _RouterNames(content, imported)
    for route in go_route_matches(_TITLE_ROUTE_RE, content):
        if names.is_router(route.receiver, route.offset):
            yield route


_ROUTE_SCOPE_RE = re.compile(
    r"""(?<!\w)(?P<outer>\w+)\s*\.\s*Route\s*\(\s*["`](?P<prefix>[^"`]*)["`]\s*,\s*"""
    r"func\s*\(\s*(?P<inner>\w+)\s+\*?\w+\s*\.\s*Router\s*\)\s*(?:error\s*)?(?P<brace>\{)"
)


@dataclass(frozen=True)
class RouteScope:
    """``outer.Route(prefix, func(inner Router) {...})``; *start*/*end* bound the body."""

    outer: str
    inner: str
    prefix: str
    start: int
    end: int


def route_scopes(content: str) -> list[RouteScope]:
    """Every ``Route`` closure in a chi or fiber file, outermost first."""
    if not _imported(content):
        return []
    out: list[RouteScope] = []
    for m in _ROUTE_SCOPE_RE.finditer(content):
        end = go_match_paren(content, m.start("brace"))
        if end > 0:
            out.append(
                RouteScope(m.group("outer"), m.group("inner"), m.group("prefix"), m.start("brace"), end)
            )
    return out

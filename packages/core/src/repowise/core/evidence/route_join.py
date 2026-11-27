"""Which function serves a production route: the route-table join.

A production source reports request latency per route template
(``route_latency`` entries: method and template, e.g. ``GET /users/:id``).
Every index stores the repository's routes (``http_routes``,
:mod:`repowise.core.workspace.repo_routes`) with the function that handles
each. :func:`match_route` joins one entry to them, pure, for ``evidence add``
and for an evidence source's batches alike.

Both sides are read into one form first (:func:`route_template`): every
parameter is ``{param}`` whatever its syntax (``{repo_id}``, ``:id``,
``<int:id>``, ``[id]``, a whole-segment ``*``), case and a trailing slash
dropped, as the contract matcher compares paths. The join then needs the
same method (a stored ``*`` route serves any method, read only when no route
names the method) and the same template, never a pattern match of one
against the other, so it is never a wrong function before recall:

* the matching routes name one handler: the row names it;
* they name more than one (or one the index could not resolve beside one it
  could): ``ambiguous_route``, no function;
* they name none (an inline handler): ``no_handler``;
* no stored route matches (a prefix the index could not see, a route from
  another service): ``unknown_route``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

#: A Werkzeug or Django converter parameter: ``<int:id>``, ``<path:p>``, ``<id>``.
_ANGLE_PARAM_RE = re.compile(r"<(?:[^<>:/]+:)?[^<>:/]+>")
#: A stored route that serves every method (a Django view, ``app.all``).
ANY_METHOD = "*"


class RouteMatch(NamedTuple):
    """What the join found for one route entry."""

    symbol_id: str | None
    #: Why no function was named (an unlinked reason), None when one was.
    reason: str | None


def route_template(path: str) -> str:
    """*path* in the form the join compares: every parameter ``{param}``.

    :func:`~repowise.core.workspace.extractors.http.paths.normalize_http_path`
    (the contract matcher's form, which the stored routes are in already),
    after a converter parameter (``<int:id>``) is read as one parameter.
    """
    # Here, not at the top: the extractors package is heavy, and only a route
    # document reaches this.
    from repowise.core.workspace.extractors.http.paths import normalize_http_path

    return normalize_http_path(_ANGLE_PARAM_RE.sub("{param}", path))


RouteIndex = Mapping[tuple[str, str], tuple[Any, ...]]


def route_index(routes: Iterable[Any]) -> dict[tuple[str, str], tuple[Any, ...]]:
    """Stored routes (``method``, ``path``, ``handler_symbol_id``) by
    ``(METHOD, template)``, built once per document."""
    out: dict[tuple[str, str], list[Any]] = {}
    for r in routes:
        out.setdefault((r.method.upper(), route_template(r.path)), []).append(r)
    return {k: tuple(v) for k, v in out.items()}


def match_route(method: str, route: str, index: RouteIndex) -> RouteMatch:
    """Join one ``route_latency`` entry to the stored routes (never a guess)."""
    template = route_template(route)
    found: Sequence[Any] = index.get((method.upper(), template), ())
    if not found:
        found = index.get((ANY_METHOD, template), ())
    if not found:
        return RouteMatch(None, "unknown_route")
    handlers = {r.handler_symbol_id for r in found}
    if len(handlers) > 1:
        return RouteMatch(None, "ambiguous_route")
    (handler,) = handlers
    return RouteMatch(handler, None if handler else "no_handler")


__all__ = ["ANY_METHOD", "RouteIndex", "RouteMatch", "match_route", "route_index", "route_template"]

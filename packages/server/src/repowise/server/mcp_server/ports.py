"""Stable names for the pure pieces a host reuses to serve the tools its own way.

A host that answers some tools from its own storage still parses ids, scopes
and queries exactly as the engine does. Each name here is the engine's own
object (the private name stays where it is), so a host imports it from this
module and never from a private one.

- :data:`LINE_ID_RE`, :data:`RANGE_ID_RE`, :data:`LINE_WINDOW`: ``get_symbol``
  ids that name a line (``path:120``) or a range (``path:10-40``), and the
  lines read around a line outside every symbol.
- :data:`SYMBOL_CAP`: rows ``get_context``'s compact symbol list carries.
- :func:`normalize_scope`: a ``get_answer`` scope in canonical form.
- :func:`nearest`, :data:`NEAREST_DIRS`: the directories a scope that matched
  nothing is pointed to, and how many; :func:`outermost`: scope prefixes
  without any another one already holds.
- :func:`by_alternative`, :data:`MAX_ALTERNATIVES`: an ``a|b`` query's rows one
  alternative at a time, and the most alternatives a query keeps.
- :data:`ENCLOSING_KINDS`, :class:`Span`: the symbols a line hit names as its
  ``in``.
- :func:`include_facets`: a call's ``include`` as known facet names (``other``
  for any other), ``None`` when it passed none, for usage events.
"""

from __future__ import annotations

from repowise.server.mcp_server._enclosing import _ENCLOSING_KINDS as ENCLOSING_KINDS
from repowise.server.mcp_server._enclosing import _Span as Span
from repowise.server.mcp_server._query_shape import _MAX_ALTERNATIVES as MAX_ALTERNATIVES
from repowise.server.mcp_server._savings.wrapper import _include_facets as include_facets
from repowise.server.mcp_server.tool_answer.scope import _NEAREST_DIRS as NEAREST_DIRS
from repowise.server.mcp_server.tool_answer.scope import _nearest as nearest
from repowise.server.mcp_server.tool_answer.scope import _outermost as outermost
from repowise.server.mcp_server.tool_answer.synthesis import _normalize_scope as normalize_scope
from repowise.server.mcp_server.tool_context.targets import _SYMBOL_CAP as SYMBOL_CAP
from repowise.server.mcp_server.tool_search.structured import _by_alternative as by_alternative
from repowise.server.mcp_server.tool_symbol.ranges import _LINE_ID_RE as LINE_ID_RE
from repowise.server.mcp_server.tool_symbol.ranges import _LINE_WINDOW as LINE_WINDOW
from repowise.server.mcp_server.tool_symbol.ranges import _RANGE_ID_RE as RANGE_ID_RE

__all__ = [
    "ENCLOSING_KINDS",
    "LINE_ID_RE",
    "LINE_WINDOW",
    "MAX_ALTERNATIVES",
    "NEAREST_DIRS",
    "RANGE_ID_RE",
    "SYMBOL_CAP",
    "Span",
    "by_alternative",
    "include_facets",
    "nearest",
    "normalize_scope",
    "outermost",
]

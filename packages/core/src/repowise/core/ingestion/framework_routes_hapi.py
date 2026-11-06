"""hapi routes: ``server.route({method, path, handler})`` and plugin prefixes.

A hapi route is an object literal, passed to ``server.route`` alone or in an
array, or exported from a module the server file passes in
(``server.route(require('./routes'))``). An object counts only with a
``method``, a ``path`` and a handler (``handler``, or one under ``options`` or
``config``): a Node ``http.request({method, path})`` options object has the
first two and no handler.

``server.register({plugin: require('./api'), routes: {prefix: '/api'}})``
serves every route of the plugin's module under ``/api``. :func:`hapi_mounts`
reads those calls, and the module ``server.route`` pulls routes from, so the
contract consumer can stitch prefixes across files. A prefix applies per
module: a module declaring two plugins registered at different prefixes is
read as one.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from .framework_routes import RouteMatch
from .framework_routes_js import (
    JsObject,
    call_arg_spans,
    js_imports,
    js_object_at,
    js_objects,
    plugin_ref,
)

_HAPI_FILE_RE = re.compile(
    r"""['"](?:@hapi/hapi|hapi)['"]|\bHapi\s*\.\s*[Ss]erver\s*\(|(?<![\w$.])server\s*\.\s*route\s*\("""
)
_HANDLER_HOLDERS = ("options", "config")

_CALL_RE = re.compile(r"(?<![\w$@.])(?P<receiver>[\w$]+)\s*\.\s*(?P<call>register|route)\s*(?P<paren>\()")


def hapi_file(content: str) -> bool:
    """Whether *content* imports hapi, builds a server, or calls ``server.route``."""
    return _HAPI_FILE_RE.search(content) is not None


def _has_handler(content: str, obj: JsObject) -> bool:
    if "handler" in obj.keys:
        return True
    held = any(k in obj.keys for k in _HANDLER_HOLDERS)
    return held and "handler" in content[obj.open : obj.close]


def hapi_routes(content: str) -> Iterator[tuple[RouteMatch, str | None]]:
    """Every route object in *content*, one row per verb (``*`` kept as is).

    The route's ``path`` is set for a plain literal; otherwise its source text
    comes second, for the caller to resolve against the file's constants.
    """
    for obj in sorted(js_objects(content), key=lambda o: o.open):
        if "method" not in obj.keys or "path" not in obj.keys:
            continue
        methods = obj.strings(content, "method")
        expr = obj.expr(content, "path")
        if not methods or not expr or not _has_handler(content, obj):
            continue
        path = obj.string(content, "path")
        for verb in methods:
            route = RouteMatch(
                verb=verb.upper(),
                path=path,
                receiver=None,
                handler=None,
                offset=obj.open,
                paren_offset=obj.open,
            )
            yield route, None if path is not None else expr


@dataclass(frozen=True)
class HapiMount:
    """A module another file serves routes from, and the prefix it gives them."""

    offset: int
    #: ``(spec, exported name)``; spec None for a name declared in the file.
    target: tuple[str | None, str]
    #: The ``prefix`` option's source text, "" when none is given, None when
    #: the plugin object and the call's options both give one.
    prefix: str | None


def _routes_prefix(content: str, obj: JsObject | None) -> str:
    """``routes: {prefix}`` of a register options object."""
    if obj is None or "routes" not in obj.keys:
        return ""
    inner = js_object_at(content, obj.keys["routes"][0] + _lead(content, obj.keys["routes"]))
    return (inner.expr(content, "prefix") or "") if inner is not None else ""


def _lead(content: str, span: tuple[int, int]) -> int:
    text = content[span[0] : span[1]]
    return len(text) - len(text.lstrip())


def _plugin_objects(
    content: str, span: tuple[int, int], imports: dict[str, tuple[str, str]], call_prefix: str
) -> Iterator[HapiMount]:
    """``{plugin, routes: {prefix}}`` objects in *span*, alone or in a list."""
    for obj in js_objects(content, span[0], span[1]):
        target = plugin_ref(content, obj.keys["plugin"][0], imports) if "plugin" in obj.keys else None
        if target is None:
            continue
        own = _routes_prefix(content, obj)
        # A prefix on the plugin object and another on the call: unreadable.
        prefix = None if own and call_prefix else own or call_prefix
        yield HapiMount(obj.open, target, prefix)


def hapi_mounts(content: str) -> Iterator[HapiMount]:
    """``register`` plugins and ``route`` modules named in *content*."""
    imports = js_imports(content)
    for m in _CALL_RE.finditer(content):
        spans = call_arg_spans(content, m.start("paren"))
        if not spans:
            continue
        first, first_end = spans[0]
        options = None
        if len(spans) > 1:
            options = js_object_at(content, spans[1][0] + _lead(content, spans[1]))
        call_prefix = _routes_prefix(content, options)
        if m.group("call") == "route":
            # `server.route(require('./routes'))`, or a route-table factory
            # `server.route(Routes(server))`.
            target = plugin_ref(content, first, imports, calls=True)
            if target is not None:
                yield HapiMount(m.start(), target, "")
            continue
        if content[first:first_end].lstrip()[:1] in "{[":
            yield from _plugin_objects(content, (first, first_end), imports, call_prefix)
            continue
        target = plugin_ref(content, first, imports)
        if target is not None:
            yield HapiMount(m.start(), target, call_prefix)

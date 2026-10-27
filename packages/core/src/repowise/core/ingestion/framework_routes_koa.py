"""Koa router prefixes: ``new Router({prefix: '/api'})``, ``router.prefix()`` and
``parent.use('/x', child.routes())``.

``@koa/router`` (formerly ``koa-router``) routes use the Express call shape,
read by ``framework_routes.express_routes``; what Koa adds is where a router is
served. A router carries its own prefix, and a parent router nests another,
in the same file or from a module that exports it
(``api.use(require('./users'))``). :func:`koa_routers` reads the first and
:func:`koa_uses` the second; the contract consumer stitches them across files.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from .framework_routes_js import JsObject, call_arg_spans, chain_receiver, js_object_at

_KOA_ROUTER_SPECS = ("@koa/router", "koa-router")
_KOA_ROUTER_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\brequire\s*\(\s*)['"](?:@koa/router|koa-router)['"]"""
)

# What the module is bound to: `import Router from '@koa/router'`,
# `const Router = require('koa-router')`.
_IMPORT_RE = re.compile(
    r"""(?:\bimport\s+(?P<imp>[\w$]+)\s+from|\b(?:const|let|var)\s+(?P<req>[\w$]+)\s*=\s*require\s*\()"""
    r"""\s*['"](?:@koa/router|koa-router)['"]"""
)
# `require('koa-router')(...)`: the module called as a factory in place.
_INLINE_FACTORY = r"""require\s*\(\s*['"](?:@koa/router|koa-router)['"]\s*\)"""
_PREFIX_CALL_RE = re.compile(r"(?<![\w$.])(?P<var>[\w$]+)\s*\.\s*prefix\s*(?P<paren>\()")
_USE_RE = re.compile(r"(?P<dot>\.)\s*use\s*(?P<paren>\()")
# `child.routes()` / `child.middleware()` mounts the router `child`.
_INNER_ROUTES_RE = re.compile(r"(?<![\w$.])([\w$]+)\s*\.\s*(?:routes|middleware)\s*\(")
_ROUTES_CALL_RE = re.compile(r"^(?P<expr>.+?)\s*\.\s*(?:routes|middleware)\s*\(\s*\)$", re.DOTALL)


def koa_router_file(content: str) -> bool:
    """Whether *content* imports ``@koa/router`` or ``koa-router``."""
    return any(spec in content for spec in _KOA_ROUTER_SPECS) and bool(
        _KOA_ROUTER_IMPORT_RE.search(content)
    )


def koa_routers(content: str) -> dict[str, str | None]:
    """``router variable -> prefix expression`` for each Koa router in *content*.

    The expression is the ``prefix`` option's (or ``prefix()`` argument's)
    source text, "" for a router with none, None when the options are not an
    object literal.
    """
    if not koa_router_file(content):
        return {}
    ctors = {m.group("imp") or m.group("req") for m in _IMPORT_RE.finditer(content)}
    alts = [re.escape(c) for c in sorted(ctors)] + [_INLINE_FACTORY]
    bind_re = re.compile(
        r"(?<![\w$.])(?P<var>[\w$]+)\s*(?::[^=;\n]+)?=\s*(?:new\s+)?(?:"
        + "|".join(alts)
        + r")\s*(?P<paren>\()"
    )
    out: dict[str, str | None] = {}
    for m in bind_re.finditer(content):
        spans = call_arg_spans(content, m.start("paren"))
        if not spans:
            out[m.group("var")] = ""
            continue
        options = _object(content, spans[0])
        out[m.group("var")] = None if options is None else options.expr(content, "prefix") or ""
    for m in _PREFIX_CALL_RE.finditer(content):
        spans = call_arg_spans(content, m.start("paren"))
        if m.group("var") in out and spans:
            out[m.group("var")] = content[spans[0][0] : spans[0][1]].strip()
    return out


def _object(content: str, span: tuple[int, int]) -> JsObject | None:
    text = content[span[0] : span[1]]
    return js_object_at(content, span[0] + len(text) - len(text.lstrip()))


@dataclass(frozen=True)
class KoaUse:
    """One ``parent.use([path,] router...)`` call."""

    offset: int
    receiver: str
    #: The path argument's source text, "" when the call has none.
    path: str
    #: Each router argument, unwrapped from ``.routes()`` / ``.middleware()``.
    routers: tuple[str, ...]
    #: Routers whose ``.routes()`` is passed inside another call
    #: (``mount('/api', r.routes())``), so where they are served is not read.
    wrapped: tuple[str, ...] = ()


def koa_uses(content: str) -> Iterator[KoaUse]:
    """Every ``.use(...)`` call in *content* that may mount a router."""
    for m in _USE_RE.finditer(content):
        receiver = chain_receiver(content, m.start("dot"))
        spans = call_arg_spans(content, m.start("paren"))
        if not receiver or not spans:
            continue
        args = [content[s:e].strip() for s, e in spans]
        path = args[0] if args[0][:1] in "'\"`" else ""
        routers: list[str] = []
        wrapped: list[str] = []
        for arg in args[1:] if path else args:
            unwrapped = _ROUTES_CALL_RE.match(arg)
            expr = unwrapped.group("expr").strip() if unwrapped else arg
            inner = _INNER_ROUTES_RE.findall(expr)
            if inner and not expr.startswith("require"):
                wrapped.extend(inner)
            else:
                routers.append(expr)
        yield KoaUse(m.start(), receiver, path, tuple(routers), tuple(wrapped))

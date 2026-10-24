"""Fastify routes: ``route({method, url})`` objects and plugin prefixes.

Fastify's shorthand ``app.get('/x', h)`` is the Express call shape and is read
by ``framework_routes.express_routes``. What Fastify adds is encapsulation: a
plugin is a function ``(instance, opts) => {...}``, and
``app.register(plugin, {prefix: '/api'})`` serves every route the plugin
declares on ``instance`` under ``/api``. Plugins nest, inline or across files.

:func:`fastify_plugins` finds the plugin functions of a file and
:func:`fastify_registers` the ``register`` calls; the contract consumer joins
them through the repo-wide mount map. A plugin wrapped in ``fastify-plugin``
(``fp(...)``) is not encapsulated, so a prefix given to it does not apply.
Not read: a plugin reached through a bare or aliased import (``@/routes``), a
spread route table, and ``@fastify/autoload`` directory prefixes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from .framework_routes import RouteMatch, match_paren
from .framework_routes_js import (
    call_arg_spans,
    chain_receiver,
    js_imports,
    js_object_at,
    js_object_named,
    plugin_ref,
)

# An import or require of Fastify, its types, or a call on a `fastify` instance.
_FASTIFY_FILE_RE = re.compile(
    r"""(?:\bfrom\s*|\brequire\s*\(\s*)['"](?:fastify|fastify-plugin|@fastify/[\w.-]+)['"]"""
    r"""|\bFastify[A-Z]\w*|\bfastify\s*\.\s*(?:get|post|put|delete|patch|route|register)\s*[(<]"""
)

#: Receivers that hold a Fastify instance where no binding is in scope.
FASTIFY_DEFAULT_NAMES = frozenset({"app", "router", "fastify", "server"})

# `instance.route({` and `instance.register(`, with an optional TS type argument.
_TYPE_ARG = r"(?:<[^()<>]*(?:<[^()<>]*>[^()<>]*)*>\s*)?"
_ROUTE_CALL_RE = re.compile(
    rf"(?<![\w$@.])(?P<receiver>[\w$]+)\s*\.\s*route\s*{_TYPE_ARG}(?P<paren>\()\s*\{{"
)
# `.register(`, its receiver read back along the chain (`app.register(a).register(b)`).
_REGISTER_RE = re.compile(r"(?P<dot>\.)\s*register\s*(?P<paren>\()")

# A function's head: `function name(p`, `async (p: T, opts) =>`, `p =>`.
_FUNCTION_RE = re.compile(r"\bfunction\b\s*\*?\s*(?P<name>[\w$]*)\s*\(\s*(?P<param>[\w$]+)")
# An arrow with a block body, found from its `=>`; the parameters are read
# back from a bounded window before it, so a long line costs nothing.
_ARROW_BODY_RE = re.compile(r"=>\s*\{")
_ARROW_PARAMS_RE = re.compile(
    r"(?:\(\s*(?P<p1>[\w$]+)\s*(?::[^,()]{0,120})?(?:,[^()]{0,200})?\)|(?<![\w$.])(?P<p2>[\w$]+))"
    r"\s*(?::\s*[^=;{()\n]{0,120})?$"
)
_ARROW_WINDOW = 300
# What names a function from just before its head.
_NAMED_BY_RE = re.compile(
    r"(?:(?:\b(?:const|let|var)\s+(?P<var>[\w$]+)\s*(?::[^=;\n]+)?=)"
    r"|(?P<default>\bexport\s+default|\bmodule\.exports\s*=)"
    r"|(?:\b(?:module\.)?exports\.(?P<cjs>[\w$]+)\s*=))"
    r"\s*(?P<fp>[\w$]+\s*\(\s*)?(?:async\s*)?$"
)
_ALIAS_RE = re.compile(
    r"(?:\bexport\s+default|\bmodule\.exports\s*=)\s*(?:(?P<fp>[\w$]+)\s*\(\s*)?(?P<name>[\w$]+)\s*\)?\s*;?\s*$",
    re.MULTILINE,
)
_FP_WRAPPERS = frozenset({"fp", "fastifyPlugin", "plugin"})


def fastify_file(content: str) -> bool:
    """Whether *content* is Fastify code: imports it, names its types, or calls
    a ``fastify`` instance."""
    return _FASTIFY_FILE_RE.search(content) is not None


@dataclass(frozen=True)
class Plugin:
    """A function a route or ``register`` call may run in."""

    body: int  # the `{` opening the body
    end: int  # its `}`
    param: str  # the instance it is handed
    names: tuple[str, ...]  # what the module exports it as (`default` included)
    fp: bool  # wrapped in fastify-plugin: shares its parent's context
    head: int  # where the function expression starts


@dataclass(frozen=True)
class Register:
    """One ``instance.register(plugin, opts)`` call."""

    receiver: str
    offset: int
    #: ``(spec, exported name)``; spec None for the same file. None when the
    #: plugin argument is an inline function, in :attr:`inline`.
    target: tuple[str | None, str] | None
    inline: Plugin | None
    #: The ``prefix`` option's source text, "" when none is given, None when the
    #: options are not an object literal this can read.
    prefix: str | None


def _named(content: str, head: int, own: str) -> tuple[list[str], bool]:
    before = content[max(0, head - 160) : head]
    m = _NAMED_BY_RE.search(before)
    names = [own] if own else []
    fp = False
    if m is not None:
        fp = bool(m.group("fp")) and m.group("fp").split("(")[0].strip() in _FP_WRAPPERS
        if m.group("var"):
            names.append(m.group("var"))
        elif m.group("cjs"):
            names.append(m.group("cjs"))
        elif m.group("default"):
            names.append("default")
    return names, fp


def _function_heads(content: str, params: set[str]) -> list[tuple[int, str, str, int]]:
    """``(head, param, own name, body brace)`` per function taking a param in *params*."""
    heads: list[tuple[int, str, str, int]] = []
    for m in _FUNCTION_RE.finditer(content):
        if m.group("param") not in params:
            continue
        paren = content.index("(", m.end("name"))
        close = match_paren(content, paren)
        brace = content.find("{", close) if close > 0 else -1
        if brace > 0:
            heads.append((m.start(), m.group("param"), m.group("name"), brace))
    for m in _ARROW_BODY_RE.finditer(content):
        window_start = max(0, m.start() - _ARROW_WINDOW)
        p = _ARROW_PARAMS_RE.search(content[window_start : m.start()])
        param = (p.group("p1") or p.group("p2")) if p else None
        if p is not None and param in params:
            heads.append((window_start + p.start(), param, "", m.end() - 1))
    return heads


def fastify_plugins(content: str, params: set[str]) -> list[Plugin]:
    """The functions of *content* whose instance parameter is in *params*."""
    aliases: dict[str, bool] = {}  # name -> wrapped in fp, for `export default name`
    for m in _ALIAS_RE.finditer(content):
        aliases[m.group("name")] = bool(m.group("fp")) and m.group("fp") in _FP_WRAPPERS

    out: list[Plugin] = []
    for head, param, own, brace in _function_heads(content, params):
        end = match_paren(content, brace)
        if end < 0:
            continue
        names, fp = _named(content, head, own)
        for name in list(names):
            if name in aliases:
                names.append("default")
                fp = fp or aliases[name]
        out.append(Plugin(brace, end, param, tuple(dict.fromkeys(names)), fp, head))
    return out


def register_receivers(content: str) -> set[str]:
    """The names ``register`` is called on in *content*."""
    found = (chain_receiver(content, m.start("dot")) for m in _REGISTER_RE.finditer(content))
    return {name for name in found if name}


def fastify_registers(content: str, plugins: list[Plugin]) -> list[Register]:
    """Every ``register`` call in *content*, its plugin and its prefix."""
    imports = js_imports(content) if "import" in content or "require" in content else {}
    out: list[Register] = []
    for m in _REGISTER_RE.finditer(content):
        receiver = chain_receiver(content, m.start("dot"))
        spans = call_arg_spans(content, m.start("paren"))
        if not receiver or not spans:
            continue
        first, first_end = spans[0]
        # The outermost function in the argument is the plugin itself.
        inline = min(
            (p for p in plugins if first <= p.head < first_end), key=lambda p: p.head, default=None
        )
        target = None if inline else plugin_ref(content, first, imports)
        if inline is None and target is None:
            continue
        prefix = _prefix_expr(content, spans[1]) if len(spans) > 1 else ""
        out.append(Register(receiver, m.start(), target, inline, prefix))
    return out


def _prefix_expr(content: str, span: tuple[int, int]) -> str | None:
    """The ``prefix`` of the options argument at *span* ("" for none)."""
    text = content[span[0] : span[1]]
    lead = span[0] + len(text) - len(text.lstrip())
    if text.strip().startswith("{"):
        obj = js_object_at(content, lead)
    else:
        name = text.strip()
        obj = js_object_named(content, name) if name.isidentifier() else None
    if obj is None:
        return None
    return obj.expr(content, "prefix") or ""


def fastify_route_objects(content: str) -> Iterator[tuple[RouteMatch, str | None]]:
    """``instance.route({method, url})`` declarations, one row per verb.

    The route's ``path`` is set for a plain literal; otherwise the url's source
    text comes second, for the caller to resolve against the file's constants.
    """
    for m in _ROUTE_CALL_RE.finditer(content):
        obj = js_object_at(content, m.end() - 1)
        if obj is None:
            continue
        methods = obj.strings(content, "method")
        key = "url" if "url" in obj.keys else "path"
        expr = obj.expr(content, key)
        if not methods or not expr:
            continue
        path = obj.string(content, key)
        for verb in methods:
            route = RouteMatch(
                verb=verb.upper(),
                path=path,
                receiver=m.group("receiver"),
                handler=None,
                offset=m.start(),
                paren_offset=m.start("paren"),
            )
            yield route, None if path is not None else expr

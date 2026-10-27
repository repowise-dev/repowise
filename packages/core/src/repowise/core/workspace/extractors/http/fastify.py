"""Fastify HTTP provider dialect: shorthand routes, ``route({...})`` objects and
``register(plugin, {prefix})`` prefixes.

A Fastify file is read here and not by the Express dialect, because its paths
depend on the plugin each route runs in. Recognition is in
``ingestion.framework_routes_fastify``; this dialect joins it across files:

* ``collect_mounts`` records, for every ``register`` of a plugin in another
  module (or a named one in the same file), the prefix it is given and the
  plugin the call itself runs in, keyed ``fastify:<module>#<export>``.
* ``extract`` composes a route's prefix by walking up from the plugin the route
  runs in: inline plugins in the file, then the registrations recorded above.

A prefix this cannot read (a variable), or a plugin registered at two different
prefixes or under two parents, makes its routes unreadable, so they are not
emitted rather than emitted at a guessed path.
"""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    express_routes,
    js_router_bindings,
)
from repowise.core.ingestion.framework_routes_fastify import (
    FASTIFY_DEFAULT_NAMES,
    Plugin,
    Register,
    fastify_file,
    fastify_plugins,
    fastify_registers,
    fastify_route_objects,
    register_receivers,
)
from repowise.core.ingestion.framework_routes_js import module_key, resolve_module

from ..base import line_at
from ..calls import file_strings, outside_comments
from ..langs import JS_TS
from .dialect import build_provider_contract
from .mounts import (
    add_mounts,
    alias_mounted,
    compose_prefix,
    declare_alias_mount,
    declare_mount,
    declared_mount,
    prefix_text,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from repowise.core.ingestion.framework_routes import RouteMatch
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# `fastify:<module>#<export>` -> the prefix a register call gives the plugin;
# `fastify-in:<module>#<export>` -> the plugin that call runs in, as
# `<module>#<name>,<name>` ("" at the top level).
_PREFIX = "fastify:"
_PARENT = "fastify-in:"
# `fastify-module:<module>`: a module some Fastify file registers, so it is a
# Fastify plugin even when it never imports Fastify.
_MODULE = "fastify-module:"
_NAMESPACE = "fastify"
# Plugins nest a few levels deep; a longer chain is a cycle.
_MAX_DEPTH = 16

# (the exported plugin a call runs in, or None; the prefix within it, or None
# when unreadable)
_Site = tuple[Plugin | None, str | None]


class _File:
    """One Fastify file's routes, plugins and registrations."""

    def __init__(self, ctx: ScanContext) -> None:
        content = ctx.content
        self.module = module_key(ctx.rel_path)
        strings = file_strings(ctx)
        objects = (
            (r, prefix_text(strings, expr) if expr else r.path)
            for r, expr in fastify_route_objects(content)
        )
        shorthand = ((r, r.path) for r in express_routes(content))
        self.routes: list[tuple[RouteMatch, str]] = [
            (r, path)
            for r, path in chain(shorthand, objects)
            if path and r.verb in HTTP_METHODS and not ctx.in_comment(r.offset)
        ]
        receivers = {r.receiver for r, _path in self.routes if r.receiver}
        self.plugins = fastify_plugins(content, receivers | register_receivers(content))
        self.registers: list[tuple[Register, str | None]] = [
            (reg, None if reg.prefix is None else prefix_text(strings, reg.prefix))
            for reg in outside_comments(ctx, fastify_registers(content, self.plugins))
        ]
        self.bound = {v for v, fw in js_router_bindings(content).items() if fw == "fastify"}
        self._inline = {r.inline.head: (r, p) for r, p in self.registers if r.inline is not None}

    def _plugin_at(self, offset: int, receiver: str | None) -> Plugin | None:
        """The innermost plugin around *offset* whose instance is *receiver*."""
        best: Plugin | None = None
        for p in self.plugins:
            inside = p.body < offset < p.end
            if p.param == receiver and inside and (best is None or p.body > best.body):
                best = p
        return best

    def site(self, offset: int, receiver: str | None, depth: int = 0) -> _Site | None:
        """Where a call on *receiver* at *offset* runs; None when *receiver*
        holds no Fastify instance there."""
        plugin = self._plugin_at(offset, receiver)
        if plugin is None:
            if receiver in self.bound or receiver in FASTIFY_DEFAULT_NAMES:
                return None, ""
            return None
        found = self._inline.get(plugin.head)
        if found is None:
            return plugin, ""  # a named or exported plugin: the mount map has its prefix
        reg, prefix = found
        outer = self.site(reg.offset, reg.receiver, depth + 1) if depth < _MAX_DEPTH else None
        exported, within = outer if outer is not None else (None, "")
        if plugin.fp:
            return exported, within
        if within is None or prefix is None:
            return exported, None
        return exported, compose_prefix(within, prefix)

    def keys(self, plugin: Plugin) -> list[str]:
        return [f"{self.module}#{name}" for name in plugin.names]


def _parent_keys(value: str) -> list[str]:
    module, _, names = value.partition("#")
    return [f"{module}#{name}" for name in names.split(",") if name]


def _resolve(mounts: Mapping[str, str], keys: list[str], fp: bool, depth: int = 0) -> str | None:
    """The prefix the plugin known by *keys* is served at; None when unreadable.

    A plugin no ``register`` call names is served where it is declared ("").
    """
    if depth > _MAX_DEPTH:
        return None
    for key in keys:
        declared, value = declared_mount(mounts, _PREFIX + key)
        if not declared:
            continue
        has_parent, parent = declared_mount(mounts, _PARENT + key)
        if value is None or (has_parent and parent is None):
            return None
        outer = _resolve(mounts, _parent_keys(parent or ""), False, depth + 1)
        if outer is None:
            return None
        return outer if fp else compose_prefix(outer, value)
    return ""


def fastify_module(ctx: ScanContext) -> bool:
    """Whether *ctx*'s file is Fastify code, or a module a Fastify file registers."""
    if fastify_file(ctx.content):
        return True
    return declared_mount(ctx.mounts, _MODULE + module_key(ctx.rel_path))[0]


class FastifyDialect:
    name = "fastify"
    extensions = JS_TS
    # A plugin module need not name Fastify at all
    # (`module.exports = async function (app) {...}`); the export admits it and
    # the mount map then has to name it before anything is read.
    requires = ("fastify", "Fastify", "export", "exports")

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """Each plugin registered from this file: its prefix and its parent."""
        if not fastify_file(ctx.content):
            return {}
        f = _File(ctx)
        out: dict[str, str] = {}
        for reg, reg_prefix in f.registers:
            if reg.target is None:
                continue
            spec, export = reg.target
            module = f.module if spec is None else resolve_module(ctx.rel_path, spec)
            exported, within = f.site(reg.offset, reg.receiver) or (None, "")
            prefix = (
                None
                if within is None or reg_prefix is None
                else compose_prefix(within, reg_prefix)
            )
            if module is None:
                if spec and prefix != "":
                    add_mounts(out, declare_alias_mount(_NAMESPACE, spec))
                continue
            parent = f"{f.module}#{','.join(exported.names)}" if exported and exported.names else ""
            key = f"{module}#{export}"
            add_mounts(out, declare_mount(_PREFIX + key, prefix))
            add_mounts(out, declare_mount(_PARENT + key, parent))
            add_mounts(out, declare_mount(_MODULE + module, ""))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if not fastify_module(ctx):
            return []
        f = _File(ctx)
        aliased = alias_mounted(ctx.mounts, _NAMESPACE, f.module)
        out: list[Contract] = []
        for route, path in f.routes:
            site = f.site(route.offset, route.receiver)
            if site is None or site[1] is None:
                continue
            exported, within = site
            keys = f.keys(exported) if exported else []
            if aliased and not any(declared_mount(ctx.mounts, _PREFIX + k)[0] for k in keys):
                continue  # registered through an alias this cannot follow
            outer = _resolve(ctx.mounts, keys, exported.fp) if exported else ""
            if outer is None:
                continue
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=compose_prefix(compose_prefix(outer, within), path),
                framework="fastify",
                line=line_at(ctx.content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out

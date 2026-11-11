"""hapi HTTP provider dialect: route objects and ``register`` prefixes.

Recognition is in ``ingestion.framework_routes_hapi``. ``collect_mounts``
records each module a file registers as a plugin or passes to ``server.route``,
with its prefix and the registering module, keyed ``hapi:<module>``; a module
recorded there is read even when it does not import hapi itself (a route table
exported as a plain array). A prefix that is not a literal, or a module mounted
at two different prefixes, leaves its routes unread rather than guessed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes_fastify import fastify_file
from repowise.core.ingestion.framework_routes_hapi import hapi_file, hapi_mounts, hapi_routes
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
    declare_module_mount,
    module_prefix,
    prefix_text,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_NAMESPACE = "hapi"
_VERBS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "*"})


class HapiDialect:
    name = "hapi"
    extensions = JS_TS
    # `handler` admits a route table module with no hapi import, which the
    # mount map then has to name before anything is read from it.
    requires = ("hapi", "Hapi", ".route(", ".route (", "handler")

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """The modules this file mounts, with their prefixes."""
        if not hapi_file(ctx.content) or fastify_file(ctx.content):
            return {}
        here = module_key(ctx.rel_path)
        strings = file_strings(ctx)
        out: dict[str, str] = {}
        for mount in outside_comments(ctx, hapi_mounts(ctx.content)):
            spec, _name = mount.target
            # A name declared in this file is read with the file itself.
            module = resolve_module(ctx.rel_path, spec) if spec else None
            prefix = prefix_text(strings, mount.prefix)
            if spec and module is None and prefix != "":
                add_mounts(out, declare_alias_mount(_NAMESPACE, spec))
            if module is None or module == here:
                continue
            add_mounts(out, declare_module_mount(_NAMESPACE, module, prefix, here))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if fastify_file(ctx.content):
            return []
        module = module_key(ctx.rel_path)
        mounted, prefix = module_prefix(ctx.mounts, _NAMESPACE, module)
        if prefix is None or not (mounted or hapi_file(ctx.content)):
            return []
        if not mounted and alias_mounted(ctx.mounts, _NAMESPACE, module):
            return []  # registered through an alias this cannot follow
        strings = file_strings(ctx)
        out: list[Contract] = []
        for route, expr in hapi_routes(ctx.content):
            path = prefix_text(strings, expr) if expr else route.path
            if not path or route.verb not in _VERBS or ctx.in_comment(route.offset):
                continue
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=compose_prefix(prefix, path),
                framework="hapi",
                line=line_at(ctx.content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out

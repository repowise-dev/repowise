"""Node router-DSL HTTP provider dialect: ``router.get('/path', ...)``.

Express, Hono, Fastify, Koa and Elysia all serve routes through this one call
shape, and only the variable's binding says which is which. The contract is
labelled from that binding rather than assumed to be Express. A Fastify file
(or a module a Fastify file registers) and a ``koa-router`` file are left to
their own dialects, because there a route's path depends on the plugin or
router it runs in.

Routers carry no in-file prefix; the mount lives in an ``app.use('/prefix',
router)`` call, often in another file, so the real path is recovered by
stitching the mount prefix onto the route. A mount of an imported router is
keyed by the module that defines it and the name it is exported under
(``express:routes/users:default``), never by the variable name alone: most
router modules call their router ``router``, and the mounting file names it
something else. A router mounted on another router composes under that
router's own mount.

The route call and the bindings both come from ``ingestion.framework_routes``,
shared with the graph-edge builders that scan the same call's argument list.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    JS_DEFAULT_ROUTER_NAMES,
    express_routes,
    js_router_bindings,
)
from repowise.core.ingestion.framework_routes_js import (
    chain_receiver,
    js_imports,
    module_key,
    resolve_module,
)
from repowise.core.ingestion.framework_routes_koa import koa_router_file

from ..base import line_at
from ..calls import code_matches, outside_comments
from ..langs import JS_TS
from .dialect import build_provider_contract
from .express_handed import (
    HandedFunctions,
    declare_handed,
    handed_calls,
    handed_key,
    is_handed,
    served_prefix,
)
from .fastify import fastify_module
from .mounts import add_mounts, compose_prefix, declare_module_mount

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# app.use('/prefix', router): a router mounted in this file or imported into it.
# The router is a name, a member (`users.router`), a Koa-style `x.routes()` or
# an inline `require('./x')`.
_APP_USE_RE = re.compile(
    r"""\.use\s*\(\s*['"]([^'"]+)['"]\s*,\s*"""
    r"""(require\s*\(\s*['"][^'"]+['"]\s*\)|[\w$.]+?)"""
    r"""(?:\.routes\s*\(\s*\)|(?![\w$.]|\s*\())"""
)
_REQUIRE_SPEC_RE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]""")
_BARE_NAME_RE = re.compile(r"[\w$]+")
_NAMESPACE_IMPORT_RE = re.compile(r"""\bimport\s+\*\s+as\s+([\w$]+)\s+from\s+['"]([^'"]+)['"]""")
# What a module exports as its default: `module.exports = x`, `export default x`
# or `export { x as default }`.
_DEFAULT_EXPORT_RE = re.compile(
    r"""(?:\bmodule\.exports\s*=\s*|\bexport\s+default\s+)([\w$]+)\s*(?:;|$)"""
    r"""|\bexport\s*\{[^}]*?\b([\w$]+)\s+as\s+default\b""",
    re.MULTILINE,
)

# Mount keys are ``express:<module>:<exported name>``.
_NAMESPACE = "express"
# Routers nest a few levels deep in one file; a longer chain is a cycle.
_MAX_DEPTH = 16


class _File:
    """One file's routes, where each router is mounted within the file, and
    the imported routers the file mounts."""

    def __init__(self, ctx: ScanContext) -> None:
        self.module = module_key(ctx.rel_path)
        self.rel_path = ctx.rel_path
        self.content = ctx.content
        self.bindings = js_router_bindings(ctx.content)
        self.routes = [
            r
            for r in outside_comments(ctx, express_routes(ctx.content))
            if r.verb in HTTP_METHODS and r.path
        ]
        # In-file mounts: router variable -> {(receiver, path)}.
        self.local: dict[str, set[tuple[str, str]]] = {}
        # Imported mounts: (module, exported name, receiver, path, offset).
        self.imported: list[tuple[str, str, str, str, int]] = []
        self._imports: dict[str, tuple[str, str]] | None = None
        self._read_mounts(ctx)
        self.default = self._default_export(ctx.content)
        # The exported functions routers are handed to, read when first asked.
        self._named: set[str] | None = None
        self.handed = HandedFunctions(ctx, self.module, self._exported_as)

    @property
    def imports(self) -> dict[str, tuple[str, str]]:
        if self._imports is None:
            self._imports = _module_bindings(self.content)
        return self._imports

    def _read_mounts(self, ctx: ScanContext) -> None:
        for m in code_matches(ctx, _APP_USE_RE):
            receiver = chain_receiver(ctx.content, m.start()) or ""
            path, target = m.group(1), m.group(2)
            source = _imported_router(target, self.imports)
            if source is None:
                if _BARE_NAME_RE.fullmatch(target):
                    self.local.setdefault(target, set()).add((receiver, path))
                continue
            module = resolve_module(ctx.rel_path, source[0])
            if module is not None and module != self.module:
                self.imported.append((module, source[1], receiver, path, m.start()))

    def _default_export(self, content: str) -> str | None:
        found = _DEFAULT_EXPORT_RE.search(content)
        if found:
            return found.group(1) or found.group(2)
        routers = set(self.bindings) | {
            r.receiver for r in self.routes if r.receiver in JS_DEFAULT_ROUTER_NAMES
        }
        if len(routers) != 1:
            return None
        # A module with one router and no default export this can read is
        # taken to export that router, unless it exports it by name.
        (sole,) = routers
        return None if _named_export_re(sole).search(content) else sole

    def key(self, var: str) -> str:
        """The mount key of router *var*: its module and the name it is imported by."""
        return _key(self.module, "default" if var == self.default else var)

    def _exported_as(self, name: str) -> list[str]:
        """The names a module-level *name* is exported under."""
        if self._named is None:
            self._named = _named_exports(self.content)
        out = ["default"] if name == self.default else []
        return [*out, name] if name in self._named else out

    def root_key(self, root: str, offset: int) -> str:
        """The mount key of router *root*, which a mount or call at *offset*
        hangs off: the parameter of the exported function around it that
        *root* names, else a router of this module."""
        handed = self.handed.keys(root, offset)
        # A function exported under two names has no one key to hang off.
        return handed[0] if len(handed) == 1 else self.key(root)

    def placed(self, var: str, depth: int = 0) -> tuple[str, str] | None:
        """``(root router, prefix within the file)`` for router *var*.

        The root is the router the in-file mount chain starts from, the one
        another file mounts. None when *var* is mounted at two places in the
        file, or the chain cycles.
        """
        mounts = self.local.get(var)
        if not mounts:
            return var, ""
        if depth > _MAX_DEPTH or len(mounts) > 1:
            return None
        receiver, path = next(iter(mounts))
        outer = self.placed(receiver, depth + 1)
        if outer is None:
            return None
        return outer[0], compose_prefix(outer[1], path)


def _key(module: str, exported: str) -> str:
    return f"{module}:{exported}"


def _module_bindings(content: str) -> dict[str, tuple[str, str]]:
    """``local name -> (spec, exported name)``, a namespace import
    (``import * as users``) read like a whole-module ``require``."""
    out = {m.group(1): (m.group(2), "default") for m in _NAMESPACE_IMPORT_RE.finditer(content)}
    out.update(js_imports(content))
    return out


def _imported_router(target: str, imports: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """``(spec, exported name)`` of the router a mount names from another
    module: an inline ``require('./x')``, an imported name, or a member of an
    imported module (``users.router``). None for anything else."""
    spec_m = _REQUIRE_SPEC_RE.match(target)
    if spec_m:
        return spec_m.group(1), "default"
    head, _, member = target.partition(".")
    if head not in imports:
        return None
    spec, exported = imports[head]
    if not member:
        return spec, exported
    # A member of a whole module; a member of a named import is not followed.
    return (spec, member.split(".")[0]) if exported == "default" else None


def _named_exports(content: str) -> set[str]:
    """Every name :func:`_named_export_re` would find exported, read in one pass."""
    out: set[str] = set()
    for m in _NAMED_EXPORTS_RE.finditer(content):
        if m.group(3) is not None:
            out.update(_IDENT_RE.findall(m.group(3)))
        else:
            out.add(m.group(1) or m.group(2))
    return out


_NAMED_EXPORTS_RE = re.compile(
    r"\bexport\s+(?:const|let|var)\s+([\w$]+)|\bexports\.([\w$]+)\s*=(?!=)"
    r"|\b(?:module\.exports\s*=|export)\s*\{([^}]*)"
)
_IDENT_RE = re.compile(r"[\w$]+")


def _named_export_re(var: str) -> re.Pattern[str]:
    """``export const var``, ``exports.var =``, or *var* in an export list
    (``module.exports = { var }``, ``export { var }``).

    Ceiling: a renamed member (``module.exports = { api: var }``) reads as
    exported under *var*, so a mount of ``x.api`` does not reach it.
    """
    v = re.escape(var)
    return re.compile(
        rf"\bexport\s+(?:const|let|var)\s+{v}\b|\bexports\.{v}\s*="
        rf"|\b(?:module\.exports\s*=|export)\s*\{{[^}}]*(?<![\w$]){v}(?![\w$])"
    )


class ExpressDialect:
    name = "express"
    extensions = JS_TS
    # A route's receiver is a default name or a router a constructor binds,
    # and a mount is a `.use(`; `app .get(` and `.use (` are allowed.
    requires = (
        "app.", "app ", "app\n", "app\t", "router.", "router ", "router\n", "router\t",
        "Router", "express", "Hono", "astify", "Koa", "koa", "Elysia",
        ".use(", ".use ", ".use\n", ".use\t",
    )  # fmt: skip

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """Each imported router this file mounts, keyed by its module and export.

        A router mounted on a router of this file is recorded under that
        router, so a module mounted by a module composes both prefixes. A
        router whose module cannot be resolved (a bare or aliased spec) is
        left unmounted.
        """
        if fastify_module(ctx) or koa_router_file(ctx.content):
            return {}
        f = _File(ctx)
        out: dict[str, str] = {}
        for module, exported, receiver, path, offset in f.imported:
            placed = f.placed(receiver)
            if placed is None:
                prefix, parent = None, ""
            else:
                prefix, parent = compose_prefix(placed[1], path), f.root_key(placed[0], offset)
            add_mounts(
                out, declare_module_mount(_NAMESPACE, _key(module, exported), prefix, parent)
            )
        if "require" in ctx.content or "import" in ctx.content:
            _handed_mounts(ctx, f, out)
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if fastify_module(ctx) or koa_router_file(ctx.content):
            return []
        f = _File(ctx)
        # `app`/`router` are routers whose constructor is in another file, so
        # they take the file's framework when it binds exactly one, and Express
        # otherwise, which is what they were always labelled.
        served = set(f.bindings.values())
        default = served.pop() if len(served) == 1 else "express"

        out: list[Contract] = []
        for route in f.routes:
            if route.receiver in f.bindings:
                framework = f.bindings[route.receiver]
            elif route.receiver in JS_DEFAULT_ROUTER_NAMES:
                framework = default
            else:
                continue
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=compose_prefix(
                    _mount_of(ctx, f, route.receiver, route.offset), route.path
                ),
                framework=framework,
                line=line_at(ctx.content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out


def _handed_mounts(ctx: ScanContext, f: _File, out: dict[str, str]) -> None:
    """Record each router this file hands to another module's exported
    function (``require('./routes/users')(api)``), at the prefix it serves
    under (see :mod:`.express_handed`)."""
    calls = handed_calls(ctx, f.imports, lambda spec: resolve_module(ctx.rel_path, spec))
    for module, exported, index, arg, offset in calls:
        if module == f.module:
            continue
        placed = f.placed(arg)
        if placed is None:
            prefix, parent = None, ""
        else:
            prefix, parent = placed[1], f.root_key(placed[0], offset)
        key = handed_key(module, exported, index)
        add_mounts(out, declare_module_mount(_NAMESPACE, key, prefix, parent))
        add_mounts(out, declare_handed(module))


def _mount_of(ctx: ScanContext, f: _File, var: str, offset: int) -> str:
    """The full prefix router *var* serves under, "" when unmounted.

    A router mounted at two different prefixes reads as unmounted, as an
    ambiguous mount always has: its routes keep their in-file path. A default
    export that is also exported by name is found under either name.

    Ceiling: a router mounted through a spec this cannot resolve (a bundler or
    tsconfig alias such as ``@/routes``) keeps its in-file path.
    """
    placed = f.placed(var)
    if placed is None:
        return ""
    root, within = placed
    if is_handed(ctx.mounts, f.module):
        # A router handed to the exported function the route sits in serves
        # wherever its call sites put it; call sites that disagree, or one
        # that cannot be read, keep the in-function path.
        served = [served_prefix(ctx.mounts, k) for k in f.handed.keys(root, offset)]
        outers = {outer for mounted, outer in served if mounted}
        if outers:
            return (
                compose_prefix(outers.pop(), within)
                if len(outers) == 1 and None not in outers
                else within
            )
    mounted, outer = served_prefix(ctx.mounts, f.key(root))
    if not mounted:
        _mounted, outer = served_prefix(ctx.mounts, _key(f.module, root))
    return compose_prefix(outer or "", within)

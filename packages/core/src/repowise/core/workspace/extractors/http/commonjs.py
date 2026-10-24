"""The string members a CommonJS module exports, for the files that ``require`` it.

Older Node services keep their service URLs in one module
(``module.exports = { cartsUrl: util.format("http://carts%s/carts", domain) }``)
and every route file reads them as ``endpoints.cartsUrl``. The members travel
through the orchestrator's mount pass keyed by module path, and a file binding
the module (``var endpoints = require("../endpoints")``) has
``endpoints.cartsUrl`` settled to the member's text. A member that does not
resolve is left out, so the name stays a ``${endpoints.cartsUrl}`` base token.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import TYPE_CHECKING

from ..langs import JS_EXTENSION_RE
from ..strings import JS_SYNTAX, object_members, resolve_string, string_constants

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..base import ScanContext

_KEY = "commonjs-exports:"
# Present when any file of the repo exports members, so a file is only looked up then.
_ANY = "commonjs-exports"
_EXPORTS_RE = re.compile(r"module\.exports\s*=\s*\{")


def _module_path(rel_path: str) -> str:
    path = JS_EXTENSION_RE.sub("", rel_path)
    return path.removesuffix("/index") if path.endswith("/index") else path


def exports_mounts(ctx: ScanContext) -> dict[str, str]:
    """The string members *ctx*'s ``module.exports = {...}`` holds, as one mount entry."""
    if "module.exports" not in ctx.content:
        return {}
    m = _EXPORTS_RE.search(ctx.content)
    if m is None or ctx.in_comment(m.start()):
        return {}
    constants = string_constants(ctx.content, JS_SYNTAX)
    members: dict[str, str] = {}
    for key, value in object_members(ctx.content, m.end() - 1, JS_SYNTAX).items():
        text = resolve_string(value, JS_SYNTAX, constants)
        if text is not None:
            members[key] = text
    if not members:
        return {}
    return {_KEY + _module_path(ctx.rel_path): json.dumps(members, sort_keys=True), _ANY: ""}


def imported_exports(ctx: ScanContext, imports: Iterable[tuple[str, str, str]]) -> dict[str, str]:
    """``local.member -> text`` for each module *ctx* binds by a relative ``require`` or default import.

    *imports* is ``(local, imported, specifier)`` per binding.
    """
    if _ANY not in ctx.mounts:
        return {}
    out: dict[str, str] = {}
    for local, imported, spec in imports:
        if imported != "default" or not spec.startswith("."):
            continue
        path = posixpath.normpath(posixpath.join(posixpath.dirname(ctx.rel_path), spec))
        value = ctx.mounts.get(_KEY + _module_path(path))
        if value is not None:
            out.update((f"{local}.{member}", text) for member, text in json.loads(value).items())
    return out


__all__ = ["exports_mounts", "imported_exports"]

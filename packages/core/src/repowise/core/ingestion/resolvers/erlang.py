"""Erlang import resolution (lightweight regex tier).

Resolution per extracted form:

- ``-include("h.hrl")`` → importer-relative probe, then the app-local and
  repo-root ``include/`` conventions, then a deterministic
  ``…/include/h.hrl`` suffix scan.
- ``-include_lib("app/include/h.hrl")`` (``lib:`` marker) → the app may be
  THIS repo (rebar umbrella ``apps/<app>/include``); otherwise a labelled
  external.
- ``-behaviour(mod)`` / bare modules → declared ``-module(x)`` index
  (flat namespace; filename stem as inverse); OTP behaviours drop after
  a local miss.
- ``call:mod`` (module-qualified call sites) → strict local-hit-or-drop:
  an edge only when the module is declared in this repo, never an
  external node — anything else would drown the graph in stdlib calls.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index, lookup_module

if TYPE_CHECKING:
    from .context import ResolverContext

_MODULE_DECL_RE = re.compile(r"^-module\(([a-z][A-Za-z0-9_]*)\)", re.M)

_OTP_MODULES = frozenset(
    {
        "application", "gen_event", "gen_fsm", "gen_server", "gen_statem", "supervisor",
        "supervisor_bridge", "ct_suite",
    }
)


def _path_to_module(path: str) -> str | None:
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem if stem and stem[0].islower() else None


def _get_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_erlang_module_index",
        extensions=(".erl",),
        declaration_re=_MODULE_DECL_RE,
        path_to_module=_path_to_module,
    )


def _resolve_include(header: str, importer_path: str, ctx: ResolverContext) -> str | None:
    importer_dir = posixpath.dirname(importer_path)
    candidates = [
        posixpath.normpath(posixpath.join(importer_dir, header)),
        # src/foo.erl including a header from its app's include/ dir
        posixpath.normpath(posixpath.join(importer_dir, "..", "include", header)),
        f"include/{header}",
        header,
    ]
    for candidate in candidates:
        if candidate in ctx.path_set:
            return candidate
    suffix = f"/include/{header}"
    for path in ctx.sorted_paths:
        if path.endswith(suffix):
            return path
    return f"external:{header}"


def _resolve_include_lib(spec: str, ctx: ResolverContext) -> str | None:
    app, _, rest = spec.partition("/")
    if rest:
        suffix = f"{app}/{rest}"
        for path in ctx.sorted_paths:
            if path == suffix or path.endswith(f"/{suffix}"):
                return path
    return f"external:{app}"


def resolve_erlang_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    if module_path.startswith("lib:"):
        return _resolve_include_lib(module_path[len("lib:") :], ctx)
    if module_path.endswith(".hrl"):
        return _resolve_include(module_path, importer_path, ctx)

    strict = module_path.startswith("call:")
    module = module_path[len("call:") :] if strict else module_path

    hit = lookup_module(_get_index(ctx), module)
    if hit and hit != importer_path:
        return hit
    if strict or hit == importer_path:
        return None  # qualified calls: local hit or nothing
    if module in _OTP_MODULES:
        return None
    return f"external:{module}"

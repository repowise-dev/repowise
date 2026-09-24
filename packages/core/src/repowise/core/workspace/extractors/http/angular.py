"""Angular's ``HttpClient`` receivers, and the environment files apps take a base from.

``HttpClient`` is injected, never called by its own name: a service calls
``this.http.get(url)`` on a receiver declared with the type
(``constructor(private http: HttpClient)``, ``private readonly api: HttpClient``)
or injected into a field (``http = inject(HttpClient)``). :func:`http_client_receivers`
names those receivers, so :mod:`.node_clients` reads their calls like any
other client's.

An Angular CLI app keeps its API base in ``src/environments/environment.ts``,
an object-literal const another module imports (``environment.apiUrl``); the
build swaps the file per configuration, and the file read here is the one the
source imports. Its string members travel through the orchestrator's mount
pass keyed by module path and fold into the importing file's URLs. A member
that does not resolve stays a ``${environment.apiUrl}`` base token.

Ceiling: an interceptor that prepends a base (``req.clone({ url: base +
req.url })``) applies to every call at run time, but which calls it reaches is
decided by provider registration, so it is not guessed; those calls keep the
path they spell.
"""

from __future__ import annotations

import json
import posixpath
import re
from functools import lru_cache
from typing import TYPE_CHECKING

from ..calls import typed_receivers
from ..langs import JS_EXTENSION_RE
from ..strings import JS_SYNTAX, resolve_string, string_constants

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..base import ScanContext

ANGULAR_HTTP = "@angular/common/http"


@lru_cache(maxsize=16)
def _type_re(type_name: str) -> re.Pattern[str]:
    return re.compile(re.escape(type_name))


def http_client_receivers(content: str, type_name: str) -> list[str]:
    """The receivers *content* declares with ``HttpClient``, imported as *type_name*."""
    return typed_receivers(content, _type_re(type_name))


# --- environment files -----------------------------------------------------

_ENV_KEY = "environment:"
# Present when any file of the repo is an environment module, so a file is
# only looked up then.
_ENV_ANY = "environment-modules"
_ENV_DIR = "environments"
_EXPORTED_CONST_RE = re.compile(r"export\s+const\s+(?P<name>[A-Za-z_$][\w$]*)")


def _module_path(rel_path: str) -> str:
    return JS_EXTENSION_RE.sub("", rel_path)


def environment_mounts(ctx: ScanContext) -> dict[str, str]:
    """The string members an environment module exports, as one mount entry.

    Only a module in an ``environments/`` directory counts: Angular CLI's
    convention, and the one directory whose objects a build swaps in whole.
    """
    parent = posixpath.basename(posixpath.dirname(ctx.rel_path))
    if parent != _ENV_DIR or "export" not in ctx.content:
        return {}
    exported = {m.group("name") for m in _EXPORTED_CONST_RE.finditer(ctx.content)}
    if not exported:
        return {}
    constants = string_constants(ctx.content, JS_SYNTAX)
    members: dict[str, str] = {}
    for name, rhs in constants.items():
        if name.partition(".")[0] in exported and "." in name:
            text = resolve_string(rhs, JS_SYNTAX, constants)
            if text is not None:
                members[name] = text
    if not members:
        return {}
    return {_ENV_KEY + _module_path(ctx.rel_path): json.dumps(members, sort_keys=True), _ENV_ANY: ""}


def _environment_module(
    rel_path: str, spec: str, mounts: Mapping[str, str]
) -> str | None:
    """The mount value of the environment module *spec* names from *rel_path*.

    A relative specifier resolves against the importing file. A ``paths``
    alias naming the environments (``@env/environment``,
    ``src/environments/environment``) is looked up as
    ``<ancestor>/environments/<name>``, nearest ancestor first, which is where
    the alias points in an Angular CLI or Nx app. Any other specifier is a
    package (``@ngrx/environment``) and is not looked up.
    """
    if spec.startswith("."):
        path = posixpath.normpath(posixpath.join(posixpath.dirname(rel_path), spec))
        return mounts.get(_ENV_KEY + path)
    folder, _, leaf = spec.rstrip("/").rpartition("/")
    if "env" not in folder:
        return None
    parts = rel_path.split("/")[:-1]
    for depth in range(len(parts), -1, -1):
        base = "/".join(parts[:depth])
        value = mounts.get(f"{_ENV_KEY}{base + '/' if base else ''}{_ENV_DIR}/{leaf}")
        if value is not None:
            return value
    return None


def imported_environment(
    ctx: ScanContext, imports: Iterable[tuple[str, str, str]]
) -> dict[str, str]:
    """``local.member -> text`` for each environment object *ctx* imports.

    *imports* is ``(local, imported, specifier)`` per binding.
    """
    if _ENV_ANY not in ctx.mounts:
        return {}
    out: dict[str, str] = {}
    for local, imported, spec in imports:
        if imported == "default":
            continue
        value = _environment_module(ctx.rel_path, spec, ctx.mounts)
        if value is None:
            continue
        for member, text in json.loads(value).items():
            name, _, rest = member.partition(".")
            if name == imported:
                out[f"{local}.{rest}"] = text
    return out


__all__ = ["ANGULAR_HTTP", "environment_mounts", "http_client_receivers", "imported_environment"]

"""Go HTTP consumer dialect: ``net/http`` calls.

Two shapes carry a verb the file can be read for:

* the package-level helpers ``http.Get`` / ``http.Head`` / ``http.Post`` /
  ``http.PostForm``, whose name is the verb and whose first argument is the
  URL;
* ``http.NewRequest(method, url, body)`` and its
  ``http.NewRequestWithContext(ctx, method, url, body)`` sibling, where the
  verb is an argument (``http.MethodGet``, ``"GET"``).

The URL argument is an expression: a literal, a raw string, a
``fmt.Sprintf`` template, a ``base + "/x"`` concatenation, or a name the file
binds once to a string. Anything the file does not settle is refused, and a
``NewRequest`` whose method argument is a runtime value (``o.Method``) is
refused too rather than given an invented verb.

Method calls on a client value (``client.Get(u)``) are not recognised: the
receiver's type is not visible to a text pass, and ``c.Get(key)`` on a cache or
a config map is far more common in Go than a request.

A URL built from a literal scheme and a struct field
(``"http://" + fe.shoppingAssistantSvcAddr``) names its host through an env
binding, usually in another file: ``mustMapEnv(&svc.shoppingAssistantSvcAddr,
"SHOPPING_ASSISTANT_SERVICE_ADDR")``. ``collect_mounts`` records those
bindings repo-wide, and the call's URL is rewritten to the base placeholder
``${SHOPPING_ASSISTANT_SERVICE_ADDR}`` so the matcher resolves its target
service the way it does any other env-keyed base. Ceiling: bindings are keyed
by field name alone (the receiver differs between the file that binds and the
file that calls), so a field two structs bind to different env keys is
ambiguous and the call is left as it was.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from ..langs import GO
from ..strings import GO_SYNTAX, call_arguments, string_constants
from .client_calls import ClientCallMatch, consumer_contracts, method_from_argument
from .mounts import declare_mount, declared_mount, declared_names

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_CLIENT = "net/http"

# http.Get( / http.Head( / http.Post( / http.PostForm(
_PACKAGE_VERB_RE = re.compile(r"\bhttp\.(Get|Head|Post|PostForm)\s*\(")

# http.NewRequest( / http.NewRequestWithContext(
_NEW_REQUEST_RE = re.compile(r"\bhttp\.NewRequest(WithContext)?\s*\(")

# Argument positions of (method, url) in each NewRequest form.
_NEW_REQUEST_ARGS = {False: (0, 1), True: (1, 2)}


def net_http_calls(content: str) -> Iterator[ClientCallMatch]:
    """Every ``net/http`` call site whose verb and URL argument are readable."""
    for m in _PACKAGE_VERB_RE.finditer(content):
        paren = m.end() - 1
        args = call_arguments(content, paren)
        if not args:
            continue
        # PostForm posts a form body; the verb it sends is still POST.
        verb = "POST" if m.group(1) == "PostForm" else m.group(1).upper()
        yield ClientCallMatch(
            client=_CLIENT,
            url=args[0],
            offset=m.start(),
            method=verb,
        )

    for m in _NEW_REQUEST_RE.finditer(content):
        paren = m.end() - 1
        args = call_arguments(content, paren)
        method_at, url_at = _NEW_REQUEST_ARGS[m.group(1) is not None]
        if args is None or len(args) <= url_at:
            continue
        method = method_from_argument(args[method_at])
        if method is None:
            continue  # a runtime method value: this layer does not know the verb
        yield ClientCallMatch(
            client=_CLIENT,
            url=args[url_at],
            offset=m.start(),
            method=method,
        )


# ---------------------------------------------------------------------------
# Env-bound struct fields
# ---------------------------------------------------------------------------

# mustMapEnv(&svc.shoppingAssistantSvcAddr, "SHOPPING_ASSISTANT_SERVICE_ADDR"):
# the field (its last segment) and the env key that fills it.
_MUST_MAP_ENV_RE = re.compile(
    r"""\bmustMapEnv\s*\(\s*&\s*(?:[A-Za-z_]\w*\.)+([A-Za-z_]\w*)\s*,\s*["`]([A-Za-z_]\w*)["`]\s*\)"""
)

# A resolved URL whose authority is one struct field: ``http://${fe.field}``,
# optionally followed by a path. The scheme is optional (``//${fe.field}``).
_AUTHORITY_FIELD_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9+.-]*:)?//\$\{\s*(?:[A-Za-z_]\w*\.)+([A-Za-z_]\w*)\s*\}"
)

# Mount-map key prefix for a field's env binding (see ``collect_mounts``).
_ENV_FIELD_PREFIX = "go-env-field:"


def env_bindings(ctx: ScanContext) -> dict[str, str | None]:
    """``field -> env key`` for each ``mustMapEnv`` binding in the file.

    A field the file binds to two different keys maps to ``None``: which one
    reaches a call is not readable, so it is refused rather than guessed.
    """
    out: dict[str, str | None] = {}
    for m in _MUST_MAP_ENV_RE.finditer(ctx.content):
        if ctx.in_comment(m.start()):
            continue
        field, key = m.groups()
        out[field] = key if out.get(field, key) == key else None
    return out


def env_base(url: str, env_of: Callable[[str], str | None]) -> str:
    """*url* with a struct-field authority rewritten to the env key's base placeholder.

    ``http://${fe.shoppingAssistantSvcAddr}/chat`` becomes
    ``${SHOPPING_ASSISTANT_SERVICE_ADDR}/chat``; the literal scheme names
    nothing the matcher can use. A field no binding settles, or text after the
    field that is not a path (a port, a query), leaves *url* as it was.
    """
    m = _AUTHORITY_FIELD_RE.match(url)
    if m is None:
        return url
    key = env_of(m.group(1))
    rest = url[m.end() :]
    if key is None or (rest and not rest.startswith("/")):
        return url
    return "${" + key + "}" + rest


class GoClientsDialect:
    name = "go-clients"
    extensions = GO
    requires = ("http.",)
    # The binding lives in the file that starts the program, which need not
    # make a request itself, so the repo-wide pass has its own gate.
    mount_requires = ("mustMapEnv",)

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """The ``mustMapEnv`` field bindings declared in the file, repo-wide once merged."""
        out: dict[str, str] = {}
        for field, key in env_bindings(ctx).items():
            out.update(declare_mount(_ENV_FIELD_PREFIX + field, key))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        rows = list(net_http_calls(ctx.content))
        if not rows:
            return []
        # The repo-wide map wins; this file's own bindings cover a scan that
        # did not build one. A field the map declares but dropped as ambiguous
        # stays refused: it must not fall back to this file's reading.
        own = env_bindings(ctx) if ctx.has_any(self.mount_requires) else {}

        def env_of(field: str) -> str | None:
            declared, value = declared_mount(ctx.mounts, _ENV_FIELD_PREFIX + field)
            return value if declared else own.get(field)

        compose = lambda url: env_base(url, env_of)  # noqa: E731
        rows = [dataclasses.replace(r, compose=compose) for r in rows]
        # The env keys a binding names: the only base placeholders that place a
        # root call. `${base}` for a local `base` is not one of them.
        fields = set(declared_names(ctx.mounts, _ENV_FIELD_PREFIX)) | set(own)
        bound = frozenset(key for f in fields if (key := env_of(f)) is not None)
        return consumer_contracts(
            ctx,
            rows,
            GO_SYNTAX,
            constants=string_constants(ctx.content, GO_SYNTAX),
            bound_bases=bound,
        )

"""JavaScript / TypeScript HTTP consumer dialect.

Covers direct ``fetch`` calls, the client libraries of :mod:`.node_clients`
(axios, ky, got, ofetch and their instances), plus wrapper calls whose first
argument is a concrete URL literal, e.g. ``fetchJSON(`${BASE}/path`, { method:
"POST" })``. Wrapper detection is signal-gated (the callee name looks HTTP-ish,
or the call carries a ``method:`` option) so ordinary `/`-prefixed string
arguments, router navigation and i18n keys, are not mistaken for service calls.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import JS_TS
from ..strings import JS_SYNTAX, literal_span, string_constants
from .client_calls import ClientCallMatch, consumer_contracts, matches_in
from .node_clients import client_calls, client_mounts

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# fetch('/api/users') or fetch('/api/users', { method: 'POST' })
_FETCH_RE = re.compile(
    r"""fetch\s*\(\s*['"`]([^'"`]+)['"`]""",
)
_FETCH_METHOD_RE = re.compile(
    r"""fetch\s*\(\s*['"`]([^'"`]+)['"`]\s*,\s*\{[^}]*method\s*:\s*['"](\w+)['"]""",
    re.DOTALL,
)

# Wrapper call: IDENT("<url>" | `<url>`, ...) where the URL literal is concrete,
# i.e. starts with `/`, a `${...}` base placeholder, or a scheme.
_WRAPPER_CALL_RE = re.compile(
    r"""\b(\w+)\s*\(\s*['"`]((?:/|\$\{|https?:)[^'"`]*)['"`]""",
)

# The callee names that read as an HTTP wrapper rather than navigation/util.
_HTTP_NAME_RE = re.compile(r"(?i)fetch|request|http|api|ajax|rest|rpc")

# A `method: "POST"` option inside the call's argument list.
_METHOD_OPT_RE = re.compile(r"""method\s*:\s*['"](\w+)['"]""")

# Callees already handled elsewhere (`ofetch(` is a fetch call), or never an HTTP wrapper.
_WRAPPER_SKIP = frozenset({"fetch", "ofetch", "if", "for", "while", "switch", "catch", "return"})


def fetch_calls(content: str) -> Iterator[ClientCallMatch]:
    """``fetch(url, { method })`` rows first, then plain ``fetch(url)`` as GET.

    A URL that appears with an explicit method anywhere in the file is not
    re-emitted as a GET.
    """
    yield from matches_in(content, _FETCH_METHOD_RE, client="fetch", url_group=1, method_group=2)
    method_urls = {m.group(1) for m in _FETCH_METHOD_RE.finditer(content)}
    for m in _FETCH_RE.finditer(content):
        if m.group(1) in method_urls:
            continue
        yield ClientCallMatch(
            client="fetch", url=literal_span(content, m, 1), offset=m.start(), callee="fetch"
        )


def _callee(content: str, start: int, end: int) -> str:
    """The whole identifier around ``content[start:end]``, a leading ``$`` included."""
    while start > 0 and (content[start - 1].isalnum() or content[start - 1] in "_$"):
        start -= 1
    return content[start:end]


def wrapper_calls(content: str, clients: frozenset[str]) -> Iterator[ClientCallMatch]:
    """``fetchJSON(`${BASE}/path`, { method: "POST" })`` and its relatives.

    *clients* are the client names the file calls, read with their base elsewhere.
    """
    for m in _WRAPPER_CALL_RE.finditer(content):
        callee = m.group(1)
        if callee in _WRAPPER_SKIP or _callee(content, m.start(1), m.end(1)) in clients:
            continue
        nl = content.find("\n", m.end())
        window = content[m.end() :] if nl == -1 else content[m.end() : nl]
        method_opt = _METHOD_OPT_RE.search(window)
        # Require an HTTP signal: an HTTP-ish callee name or a method option.
        if not (_HTTP_NAME_RE.search(callee) or method_opt):
            continue
        # No `method:` option: the callee name is the only verb evidence there
        # is, and `apiPost`/`apiDelete` carry it.
        yield ClientCallMatch(
            client="wrapper",
            url=literal_span(content, m, 2),
            offset=m.start(),
            callee=callee,
            method=method_opt.group(1).upper() if method_opt else None,
            confidence=0.65,
        )


class JsClientsDialect:
    name = "js-clients"
    extensions = JS_TS

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        return client_mounts(ctx)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        from_clients, clients = client_calls(ctx)
        # A client named like `apifetch` is read with its base above, not as `fetch`.
        fetches = [m for m in fetch_calls(content) if _callee(content, m.offset, m.offset + 5) not in clients]
        matches = [*fetches, *wrapper_calls(content, clients)]
        # A `${BASE}` interpolation folds when the file binds BASE to a constant.
        constants = string_constants(content, JS_SYNTAX) if any("${" in m.url for m in matches) else {}
        return from_clients + consumer_contracts(ctx, matches, JS_SYNTAX, constants=constants)

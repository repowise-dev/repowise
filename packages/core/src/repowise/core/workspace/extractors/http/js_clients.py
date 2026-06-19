"""JavaScript / TypeScript HTTP consumer dialect.

Covers direct ``fetch`` / ``axios`` calls plus wrapper calls whose first
argument is a concrete URL literal — e.g. ``fetchJSON(`${BASE}/path`, { method:
"POST" })``. Wrapper detection is signal-gated (the callee name looks HTTP-ish,
or the call carries a ``method:`` option) so ordinary `/`-prefixed string
arguments — router navigation, i18n keys — are not mistaken for service calls.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import JS_TS
from .dialect import METHODS, build_consumer_contract

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

# axios.get('/api/users')
_AXIOS_RE = re.compile(
    rf"""axios\.({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
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

# Callees already handled elsewhere, or never an HTTP wrapper.
_WRAPPER_SKIP = frozenset({"fetch", "if", "for", "while", "switch", "catch", "return"})


class JsClientsDialect:
    name = "js-clients"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []

        # fetch() with an explicit method.
        for m in _FETCH_METHOD_RE.finditer(content):
            out.append(
                build_consumer_contract(
                    ctx, method=m.group(2).upper(), url=m.group(1), client="fetch"
                )
            )

        # fetch() without a method → GET, skipping URLs already matched above.
        method_urls = {m.group(1) for m in _FETCH_METHOD_RE.finditer(content)}
        for m in _FETCH_RE.finditer(content):
            url = m.group(1)
            if url in method_urls:
                continue
            out.append(build_consumer_contract(ctx, method="GET", url=url, client="fetch"))

        # axios.<method>()
        for m in _AXIOS_RE.finditer(content):
            out.append(
                build_consumer_contract(
                    ctx, method=m.group(1).upper(), url=m.group(2), client="axios"
                )
            )

        # Wrapper calls — fetchJSON(`${BASE}/path`, { method: "POST" }) etc.
        for m in _WRAPPER_CALL_RE.finditer(content):
            callee = m.group(1)
            if callee in _WRAPPER_SKIP:
                continue
            nl = content.find("\n", m.end())
            window = content[m.end() :] if nl == -1 else content[m.end() : nl]
            method_opt = _METHOD_OPT_RE.search(window)
            # Require an HTTP signal: an HTTP-ish callee name or a method option.
            if not (_HTTP_NAME_RE.search(callee) or method_opt):
                continue
            method = method_opt.group(1).upper() if method_opt else "GET"
            out.append(
                build_consumer_contract(
                    ctx, method=method, url=m.group(2), client="wrapper", confidence=0.65
                )
            )

        return out

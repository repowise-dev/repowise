"""JavaScript / TypeScript HTTP consumer dialect — ``fetch`` and ``axios``."""

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

        return out

"""C# HTTP consumer dialect — ``HttpClient`` ``client.GetAsync("/api/users")``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import CSHARP
from .dialect import METHODS, build_consumer_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# client.GetAsync("/api/users") / PostAsync / PutAsync / DeleteAsync.
_HTTPCLIENT_RE = re.compile(
    rf"""\.\s*({METHODS})Async\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


class CSharpHttpDialect:
    name = "csharp-http"
    extensions = CSHARP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _HTTPCLIENT_RE.finditer(ctx.content):
            url = m.group(2)
            if "/" not in url:
                continue  # Avoid matching non-URL strings.
            out.append(
                build_consumer_contract(
                    ctx,
                    method=m.group(1).upper(),
                    url=url,
                    client="httpclient",
                    confidence=0.70,
                )
            )
        return out

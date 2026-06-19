"""Python HTTP consumer dialect — ``requests`` and ``httpx``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import PYTHON
from .dialect import METHODS, build_consumer_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# requests.get('http://host/api/users') or httpx.post(...)
_REQUESTS_RE = re.compile(
    rf"""(?:requests|httpx)\.({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


class PythonClientsDialect:
    name = "python-clients"
    extensions = PYTHON

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _REQUESTS_RE.finditer(ctx.content):
            out.append(
                build_consumer_contract(
                    ctx, method=m.group(1).upper(), url=m.group(2), client="requests"
                )
            )
        return out

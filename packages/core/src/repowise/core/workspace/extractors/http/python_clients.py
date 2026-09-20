"""Python HTTP consumer dialect: ``requests`` and ``httpx``."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import PYTHON
from .client_calls import PYTHON_SYNTAX, ClientCallMatch, consumer_contracts, matches_in
from .dialect import METHODS

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# requests.get('http://host/api/users') or httpx.post(...)
_REQUESTS_RE = re.compile(
    rf"""(?:requests|httpx)\.({METHODS})\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


def requests_calls(content: str) -> Iterator[ClientCallMatch]:
    yield from matches_in(content, _REQUESTS_RE, client="requests", url_group=2, method_group=1)


class PythonClientsDialect:
    name = "python-clients"
    extensions = PYTHON

    def extract(self, ctx: ScanContext) -> list[Contract]:
        return consumer_contracts(ctx, requests_calls(ctx.content), PYTHON_SYNTAX)

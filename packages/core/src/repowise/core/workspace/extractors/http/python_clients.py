"""Python HTTP consumer dialect: ``requests``, ``httpx`` and Locust's user client.

A Locust task calls the system under test through its user's client
(``self.client.get("/cart")``, ``l.client.post("/cart/empty")``); the path is
relative to the host the run targets, so only a rooted path is kept. Read
only in a file that imports ``locust``, where ``<x>.client`` is that client.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import PYTHON
from ..strings import PYTHON_SYNTAX, call_arguments
from .client_calls import ClientCallMatch, consumer_contracts
from .dialect import METHODS

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# requests.get('http://host/api/users') or httpx.post(...): the verb and the
# call's opening parenthesis; the URL is read off the first argument.
_REQUESTS_RE = re.compile(rf"(?:requests|httpx)\.({METHODS})\s*\(", re.IGNORECASE)

_LOCUST_IMPORT_RE = re.compile(r"^[ \t]*(?:from|import)[ \t]+locust\b", re.MULTILINE)
# `self.client.get(` / `l.client.post(`: the verb and the call's opening parenthesis.
_LOCUST_CALL_RE = re.compile(rf"\.client\s*\.\s*({METHODS}|head|options)\s*\(")


def requests_calls(content: str) -> Iterator[ClientCallMatch]:
    """``requests.<verb>(url, ...)``: a literal, an f-string or a concatenation as the URL."""
    for m in _REQUESTS_RE.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if args:
            yield ClientCallMatch(
                client="requests", url=args[0], offset=m.start(), method=m.group(1).upper()
            )


def locust_calls(content: str) -> Iterator[ClientCallMatch]:
    """``<user>.client.<verb>(path, ...)`` in a locustfile; the path is the first argument."""
    if _LOCUST_IMPORT_RE.search(content) is None:
        return
    for m in _LOCUST_CALL_RE.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if args:
            yield ClientCallMatch(
                client="locust", url=args[0], offset=m.start(), method=m.group(1).upper()
            )


class PythonClientsDialect:
    name = "python-clients"
    extensions = PYTHON
    requires = ("requests", "httpx", "Requests", "Httpx", "REQUESTS", "HTTPX", "locust")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out = consumer_contracts(ctx, requests_calls(content), PYTHON_SYNTAX)
        if "locust" in content:
            out += consumer_contracts(ctx, locust_calls(content), PYTHON_SYNTAX, rooted_only=True)
        return out

"""C# HTTP consumer dialect.

Recognises the call shapes common in C# / Unity service clients:

* ``HttpClient`` and wrapper methods: ``GetAsync`` / ``PostAsync`` /
  ``GetRequest<T>`` / ``PostRequest<T>`` (with or without a generic type arg);
* ``UnityWebRequest.Get/Post/Put/Delete`` with a literal or interpolated URL;
* Best.HTTP: ``new HTTPRequest(new Uri("..."), HTTPMethods.Get)``.

An interpolated string (``$"{_baseUrl}/path/{id}"``) resolves through the
shared C# syntax table, so the leading base placeholder is stripped and
interior expressions collapse to ``{param}``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import CSHARP
from .client_calls import CSHARP_SYNTAX, ClientCallMatch, consumer_contracts, matches_in

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# A C# string-literal argument with an optional interpolation (`$`) / verbatim
# (`@`) prefix: capture group 1 = prefix, group 2 = the inner text.
_STR = r"""(\$?@?)"([^"]*)\""""

# HttpClient + wrapper calls: GetAsync / PostAsync / GetRequest<T> / PostRequest.
# Method verbs are PascalCase (C# convention); the `Async`/`Request` suffix is
# required so we don't match unrelated `Get("key")` lookups.
_WRAPPER_RE = re.compile(
    rf"""\b(Get|Post|Put|Delete|Patch)(?:Async|Request)\s*(?:<[^>]+>)?\s*\(\s*{_STR}"""
)

# UnityWebRequest.Get(...) / .Post(...) — only when the first arg is a string.
_UNITY_RE = re.compile(rf"""\bUnityWebRequest\.(Get|Post|Put|Delete|Head)\s*\(\s*{_STR}""")

# Best.HTTP: new HTTPRequest(new Uri("..."), HTTPMethods.Get) — URL first, then
# the method enum.
_BESTHTTP_RE = re.compile(
    rf"""\bnew\s+HTTPRequest\s*\(\s*new\s+Uri\s*\(\s*{_STR}\s*\)\s*,\s*HTTPMethods\.(Get|Post|Put|Delete|Patch|Head)""",
    re.IGNORECASE,
)

_CONFIDENCE = 0.70


def httpclient_calls(content: str) -> Iterator[ClientCallMatch]:
    yield from matches_in(
        content,
        _WRAPPER_RE,
        client="httpclient",
        url_group=3,
        prefix_group=2,
        method_group=1,
        confidence=_CONFIDENCE,
    )


def unitywebrequest_calls(content: str) -> Iterator[ClientCallMatch]:
    yield from matches_in(
        content,
        _UNITY_RE,
        client="unitywebrequest",
        url_group=3,
        prefix_group=2,
        method_group=1,
        confidence=_CONFIDENCE,
    )


def besthttp_calls(content: str) -> Iterator[ClientCallMatch]:
    yield from matches_in(
        content,
        _BESTHTTP_RE,
        client="besthttp",
        url_group=2,
        prefix_group=1,
        method_group=3,
        confidence=_CONFIDENCE,
    )


class CSharpHttpDialect:
    name = "csharp-http"
    extensions = CSHARP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        matches = [
            *httpclient_calls(content),
            *unitywebrequest_calls(content),
            *besthttp_calls(content),
        ]
        # A slash-free string is a key or a name, not a route.
        return consumer_contracts(ctx, matches, CSHARP_SYNTAX, path_only=True)

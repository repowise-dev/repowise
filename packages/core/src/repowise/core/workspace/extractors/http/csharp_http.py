"""C# HTTP consumer dialect.

Recognises the call shapes common in C# / Unity service clients:

* ``HttpClient`` and wrapper methods: ``GetAsync`` / ``PostAsync`` /
  ``GetRequest<T>`` / ``PostRequest<T>`` (with or without a generic type arg);
* ``UnityWebRequest.Get/Post/Put/Delete`` with a literal or interpolated URL;
* Best.HTTP: ``new HTTPRequest(new Uri("..."), HTTPMethods.Get)``;
* verb calls inside subclasses of a configured typed root client
  (``contracts.api_client_bases``), prefixed with the base path the subclass
  passes up its constructor chain.

An interpolated string (``$"{_baseUrl}/path/{id}"``) resolves through the
shared C# syntax table, so the leading base placeholder is stripped and
interior expressions collapse to ``{param}``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import CSHARP
from ..strings import CSHARP_SYNTAX, call_arguments, resolve_string
from .client_calls import ClientCallMatch, consumer_contracts, matches_in
from .csharp_api_clients import CSHARP_APICLIENT_SYNTAX

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

# The root client's protected verbs, called bare or through this/base. A call on
# any other receiver (`_http.GetAsync`) is left to the HttpClient recogniser.
_API_CLIENT_VERB_RE = re.compile(
    r"(?<![\w.])(?:(?:this|base)\s*\.\s*)?(Get|Post|Put|Delete)Async\s*(?:<[^(){};]*>)?\s*\("
)
_NAMEOF_RE = re.compile(r"^nameof\s*\(\s*(?:[A-Za-z_]\w*\s*\.\s*)*([A-Za-z_]\w*)\s*\)$")
_API_CLIENT_CONFIDENCE = 0.80
# The host comes from the client's configuration, so the URL is base-stripped.
_API_CLIENT_BASE = "${ApiClient}"


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


def api_client_calls(ctx: ScanContext) -> list[ClientCallMatch]:
    """Verb calls inside resolved typed-client subclasses, under their base path."""
    index = ctx.api_clients
    spans = index.by_file.get(ctx.rel_path, ()) if index is not None else ()
    if index is None or not spans:
        return []
    matches: list[ClientCallMatch] = []
    for m in _API_CLIENT_VERB_RE.finditer(ctx.content):
        owner = max(
            (s for s in spans if s.start < m.start() < s.end), key=lambda s: s.start, default=None
        )
        args = call_arguments(ctx.content, m.end() - 1)
        if owner is None or not args or any(a < m.start() < b for a, b in owner.holes):
            continue
        nameof = _NAMEOF_RE.match(args[0].strip())
        endpoint = (
            nameof.group(1)
            if nameof
            else resolve_string(
                args[0], CSHARP_APICLIENT_SYNTAX, index.constants_for(args[0], owner.scope)
            )
        )
        # An unresolved leading segment would read as a path parameter.
        if endpoint is None or endpoint.startswith("${") or "://" in endpoint:
            continue
        parts = [p for p in (owner.base_path.strip("/"), endpoint.strip("/")) if p]
        matches.append(
            ClientCallMatch(
                client="apiclient",
                url=f'"{_API_CLIENT_BASE}/{"/".join(parts)}"',
                offset=m.start(1),
                method=m.group(1).upper(),
                confidence=_API_CLIENT_CONFIDENCE,
            )
        )
    return matches


class CSharpHttpDialect:
    name = "csharp-http"
    extensions = CSHARP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        api_matches = api_client_calls(ctx)
        claimed = {m.offset for m in api_matches}
        matches = [
            *(m for m in httpclient_calls(content) if m.offset not in claimed),
            *unitywebrequest_calls(content),
            *besthttp_calls(content),
        ]
        # A slash-free string is a key or a name, not a route.
        return [
            *consumer_contracts(ctx, matches, CSHARP_SYNTAX, path_only=True),
            *consumer_contracts(ctx, api_matches, CSHARP_SYNTAX),
        ]

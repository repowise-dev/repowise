"""C# HTTP consumer dialect.

Recognises the call shapes common in C# / Unity service clients:

* ``HttpClient`` and wrapper methods — ``GetAsync`` / ``PostAsync`` /
  ``GetRequest<T>`` / ``PostRequest<T>`` (with or without a generic type arg);
* ``UnityWebRequest.Get/Post/Put/Delete`` with a literal or interpolated URL;
* Best.HTTP — ``new HTTPRequest(new Uri("..."), HTTPMethods.Get)``.

C# interpolated strings (``$"{_baseUrl}/path/{id}"``) are normalised by
rewriting ``{expr}`` to the ``${expr}`` template form the shared path helpers
already understand, so the leading base placeholder is stripped and interior
expressions collapse to ``{param}``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import CSHARP
from .dialect import build_consumer_contract

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


def _to_template(prefix: str, text: str) -> str:
    """Rewrite a C# interpolated string body into ``${expr}`` template form.

    For a non-interpolated string the text is returned unchanged.
    """
    if "$" in prefix:
        return text.replace("{", "${")
    return text


class CSharpHttpDialect:
    name = "csharp-http"
    extensions = CSHARP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []

        def emit(method: str, prefix: str, text: str, client: str) -> None:
            url = _to_template(prefix, text)
            if "/" not in url:
                return  # Not a URL path — skip non-route strings.
            c = build_consumer_contract(
                ctx, method=method.upper(), url=url, client=client, confidence=0.70
            )
            if c is not None:
                out.append(c)

        for m in _WRAPPER_RE.finditer(content):
            emit(m.group(1), m.group(2), m.group(3), "httpclient")
        for m in _UNITY_RE.finditer(content):
            emit(m.group(1), m.group(2), m.group(3), "unitywebrequest")
        for m in _BESTHTTP_RE.finditer(content):
            # Group order: prefix, text, method (method trails the URL here).
            emit(m.group(3), m.group(1), m.group(2), "besthttp")

        return out

"""Go HTTP consumer dialect: ``net/http`` calls.

Two shapes carry a verb the file can be read for:

* the package-level helpers ``http.Get`` / ``http.Head`` / ``http.Post`` /
  ``http.PostForm``, whose name is the verb and whose first argument is the
  URL;
* ``http.NewRequest(method, url, body)`` and its
  ``http.NewRequestWithContext(ctx, method, url, body)`` sibling, where the
  verb is an argument (``http.MethodGet``, ``"GET"``).

The URL argument is an expression: a literal, a raw string, a
``fmt.Sprintf`` template, a ``base + "/x"`` concatenation, or a name the file
binds once to a string. Anything the file does not settle is refused, and a
``NewRequest`` whose method argument is a runtime value (``o.Method``) is
refused too rather than given an invented verb.

Method calls on a client value (``client.Get(u)``) are not recognised: the
receiver's type is not visible to a text pass, and ``c.Get(key)`` on a cache or
a config map is far more common in Go than a request.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import GO
from .client_calls import (
    GO_SYNTAX,
    ClientCallMatch,
    call_arguments,
    consumer_contracts,
    method_from_argument,
    string_constants,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_CLIENT = "net/http"

# http.Get( / http.Head( / http.Post( / http.PostForm(
_PACKAGE_VERB_RE = re.compile(r"\bhttp\.(Get|Head|Post|PostForm)\s*\(")

# http.NewRequest( / http.NewRequestWithContext(
_NEW_REQUEST_RE = re.compile(r"\bhttp\.NewRequest(WithContext)?\s*\(")

# Argument positions of (method, url) in each NewRequest form.
_NEW_REQUEST_ARGS = {False: (0, 1), True: (1, 2)}


def _commented_out(content: str, offset: int) -> bool:
    """True when a ``//`` comment opens on *offset*'s line before it.

    Go doc comments show usage as indented example code, so without this a
    ``//  req, _ := http.NewRequest("GET", "https://host/x", nil)`` in a
    docstring becomes a contract. A ``//`` that follows a colon is a URL's
    scheme separator, not a comment.
    """
    prefix = content[content.rfind("\n", 0, offset) + 1 : offset]
    idx = prefix.find("//")
    while idx != -1:
        if idx == 0 or prefix[idx - 1] != ":":
            return True
        idx = prefix.find("//", idx + 2)
    return False


def net_http_calls(content: str) -> Iterator[ClientCallMatch]:
    """Every ``net/http`` call site whose verb and URL argument are readable."""
    for m in _PACKAGE_VERB_RE.finditer(content):
        if _commented_out(content, m.start()):
            continue
        paren = m.end() - 1
        args = call_arguments(content, paren)
        if not args:
            continue
        # PostForm posts a form body; the verb it sends is still POST.
        verb = "POST" if m.group(1) == "PostForm" else m.group(1).upper()
        yield ClientCallMatch(
            client=_CLIENT,
            url=args[0],
            offset=m.start(),
            method=verb,
        )

    for m in _NEW_REQUEST_RE.finditer(content):
        if _commented_out(content, m.start()):
            continue
        paren = m.end() - 1
        args = call_arguments(content, paren)
        method_at, url_at = _NEW_REQUEST_ARGS[m.group(1) is not None]
        if args is None or len(args) <= url_at:
            continue
        method = method_from_argument(args[method_at])
        if method is None:
            continue  # a runtime method value: this layer does not know the verb
        yield ClientCallMatch(
            client=_CLIENT,
            url=args[url_at],
            offset=m.start(),
            method=method,
        )


class GoClientsDialect:
    name = "go-clients"
    extensions = GO

    def extract(self, ctx: ScanContext) -> list[Contract]:
        rows = list(net_http_calls(ctx.content))
        if not rows:
            return []
        return consumer_contracts(
            ctx, rows, GO_SYNTAX, constants=string_constants(ctx.content, GO_SYNTAX)
        )

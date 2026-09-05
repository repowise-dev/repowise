"""Ruby HTTP consumer dialect: HTTParty, RestClient, Faraday and Net::HTTP.

The four libraries share one call shape, a module or connection receiver with
the verb as the method name, so one argument reader serves all of them:

* ``HTTParty.get(CLEARBIT_ENDPOINT, options)`` and its ``post``/``put``/
  ``patch``/``delete`` siblings, the URL a constant, an interpolated string
  (``"#{BASE_URL}/accounts/#{id}"``) or a concatenation;
* ``RestClient.post(url, body, headers)``, same shape, label ``restclient``;
* ``Faraday.get(url)`` on the module, and ``conn.get("/x")`` on a connection
  the file binds with ``conn = Faraday.new(...)``;
* ``Net::HTTP.get(URI(url))`` / ``get_response`` / ``post``, and the request
  object form ``http.request(Net::HTTP::Get.new(uri))`` where the verb is a
  class name.

Ruby allows the parentheses to be omitted, so an argument list that does not
start with ``(`` is read to the first top-level comma or the end of the line.

A URL the file does not settle is refused: a helper method (``ping_url``), a
parameter, and the ``uri`` local a ``Net::HTTP.start`` block receives all yield
no contract, because a guessed path is a wrong edge.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import RUBY
from .client_calls import (
    RUBY_SYNTAX,
    ClientCallMatch,
    call_arguments,
    consumer_contracts,
    match_paren,
    method_from_argument,
    split_first_arg,
    string_constants,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_VERBS = r"get|post|put|patch|delete|head|options"

# The module receivers, each mapped to the client label its rows carry.
_MODULE_CLIENTS = {"HTTParty": "httparty", "RestClient": "restclient", "Faraday": "faraday"}

_MODULE_CALL_RE = re.compile(rf"\b(HTTParty|RestClient|Faraday)\.({_VERBS})\b")

# ``get_response`` leads the alternation so it is not read as ``get``.
_NET_HTTP_RE = re.compile(rf"\bNet::HTTP\.(get_response|{_VERBS})\b")

# ``http.request(Net::HTTP::Get.new(uri))``: the verb is the class name.
_REQUEST_OBJECT_RE = re.compile(r"\.request\s*\(\s*(Net::HTTP::\w+)\.new\s*\(")

_FARADAY_NEW_RE = re.compile(r"(?<![.\w])(@?[a-z_]\w*)\s*=\s*Faraday\.new\b")

_CONNECTION_CONFIDENCE = 0.65


def _first_argument(content: str, end: int) -> str | None:
    """The URL argument of the call whose callee ends at *end*.

    Ruby lets the parentheses go, so without one the argument runs to the
    first top-level comma or the end of the line.
    """
    i = end
    while i < len(content) and content[i] in " \t":
        i += 1
    if i < len(content) and content[i] == "(":
        close = match_paren(content, i)
        if close < 0:
            return None
        first, _ = split_first_arg(content[i + 1 : close])
        return first or None
    line_end = content.find("\n", i)
    if line_end < 0:
        line_end = len(content)
    first, _ = split_first_arg(content[i:line_end])
    return first or None


def module_calls(content: str) -> Iterator[ClientCallMatch]:
    """``HTTParty.get(...)`` / ``RestClient.post(...)`` / ``Faraday.get(...)``."""
    for m in _MODULE_CALL_RE.finditer(content):
        url = _first_argument(content, m.end())
        if url is None:
            continue
        yield ClientCallMatch(
            client=_MODULE_CLIENTS[m.group(1)],
            url=url,
            offset=m.start(),
            method=m.group(2).upper(),
        )


def net_http_calls(content: str) -> Iterator[ClientCallMatch]:
    """``Net::HTTP.get(URI(url))`` and ``http.request(Net::HTTP::Get.new(uri))``."""
    for m in _NET_HTTP_RE.finditer(content):
        url = _first_argument(content, m.end())
        if url is None:
            continue
        verb = m.group(1)
        yield ClientCallMatch(
            client="net-http",
            url=url,
            offset=m.start(),
            method="GET" if verb == "get_response" else verb.upper(),
        )
    for m in _REQUEST_OBJECT_RE.finditer(content):
        method = method_from_argument(m.group(1))
        args = call_arguments(content, m.end() - 1)
        if method is None or not args:
            continue
        yield ClientCallMatch(
            client="net-http",
            url=args[0],
            offset=m.start(),
            method=method,
        )


def faraday_connection_calls(content: str) -> Iterator[ClientCallMatch]:
    """``conn.get("/x")`` where the file binds ``conn`` with ``Faraday.new``."""
    receivers = {m.group(1) for m in _FARADAY_NEW_RE.finditer(content)}
    if not receivers:
        return
    alternation = "|".join(re.escape(r) for r in sorted(receivers, key=len, reverse=True))
    # The parenthesis is required here, unlike the module forms: an unqualified
    # receiver reads as a request only when it is called, so `conn.options` (a
    # Faraday connection's settings object) is not mistaken for an OPTIONS call.
    rx = re.compile(rf"(?<![.\w@])({alternation})\.({_VERBS})\s*\(")
    for m in rx.finditer(content):
        args = call_arguments(content, m.end() - 1)
        if not args:
            continue
        yield ClientCallMatch(
            client="faraday",
            url=args[0],
            offset=m.start(),
            method=m.group(2).upper(),
            confidence=_CONNECTION_CONFIDENCE,
        )


class RubyClientsDialect:
    name = "ruby-clients"
    extensions = RUBY

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        rows = [*module_calls(content), *net_http_calls(content)]
        connection_rows = list(faraday_connection_calls(content))
        if not rows and not connection_rows:
            return []
        constants = string_constants(content, RUBY_SYNTAX)
        out = consumer_contracts(ctx, rows, RUBY_SYNTAX, constants=constants)
        # A connection variable's `.get` could be a hash lookup, so a URL with
        # no slash in it is not a route.
        out += consumer_contracts(
            ctx,
            connection_rows,
            RUBY_SYNTAX,
            constants=constants,
            path_only=True,
            rooted_only=True,
        )
        return out

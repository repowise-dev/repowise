"""PHP HTTP consumer dialect: Guzzle and the Laravel ``Http`` facade.

Recognises the two shapes PHP application code actually uses:

* Guzzle's generic form, ``$client->request('GET', $url, ...)``, where the verb
  is the first argument and the URL the second, and its verb-named form,
  ``$client->get($url)``. The generic form is read only in a file that names
  Guzzle, because ``->request(...)`` and ``->get(...)`` are common method names
  on anything.
* The Laravel facade, ``Http::get($url)`` and its chained builders
  (``Http::withToken($t)->post($url)``), read from ``Http::`` forward along the
  chain to the call that names a verb.

The URL argument is handed to the shared resolver, so a literal, an
interpolated string and a concatenation onto a folded constant all resolve,
while a property (``$this->uri``) or a ``config(...)`` lookup is refused: PHP
does not settle either one inside the file.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import PHP
from .client_calls import (
    PHP_SYNTAX,
    VERBS,
    ClientCallMatch,
    call_arguments,
    consumer_contracts,
    match_paren,
    method_from_argument,
    string_constants,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# A receiver the call hangs off: a variable or one property of one.
_RECEIVER = r"\$[A-Za-z_]\w*(?:->[A-Za-z_]\w*)?"

_GUZZLE_REQUEST_RE = re.compile(rf"{_RECEIVER}\s*->\s*request\s*\(")
_GUZZLE_VERB_RE = re.compile(rf"{_RECEIVER}\s*->\s*(get|post|put|patch|delete|head)\s*\(")

# Guzzle is only claimed in a file that names a client: the client class, a
# construction, or the interface a constructor is typed against. Naming the
# package alone is not enough: a file importing only a PSR-7 helper from it
# still calls ``->get(...)`` on things that are not HTTP clients.
_GUZZLE_GATE_RE = re.compile(r"GuzzleHttp\\Client|new\s+(?:Guzzle)?Client\s*\(|\bClientInterface\b")

_HTTP_FACADE_RE = re.compile(r"\bHttp::")
_CHAIN_LINK_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")
_CHAIN_ARROW_RE = re.compile(r"\s*->\s*")

# How far along a builder chain the verb call is looked for.
_MAX_CHAIN_LINKS = 8

_AMBIGUOUS_RECEIVER_CONFIDENCE = 0.65


def _names_a_client(content: str) -> bool:
    return "Client" in content and _GUZZLE_GATE_RE.search(content) is not None


def guzzle_request_calls(content: str) -> Iterator[ClientCallMatch]:
    """``$client->request('GET', $url, ...)``: verb first, URL second."""
    if not _names_a_client(content):
        return
    for m in _GUZZLE_REQUEST_RE.finditer(content):
        open_idx = m.end() - 1
        args = call_arguments(content, open_idx)
        if args is None or len(args) < 2:
            continue
        method = method_from_argument(args[0])
        if method is None:
            continue  # a runtime verb: this layer has no way to know it
        yield ClientCallMatch(
            client="guzzle",
            url=args[1],
            offset=m.start(),
            method=method,
        )


def guzzle_verb_calls(content: str) -> Iterator[ClientCallMatch]:
    """``$client->get($url)``: the verb is the callee's name."""
    if not _names_a_client(content):
        return
    for m in _GUZZLE_VERB_RE.finditer(content):
        open_idx = m.end() - 1
        args = call_arguments(content, open_idx)
        if not args:
            continue
        yield ClientCallMatch(
            client="guzzle",
            url=args[0],
            offset=m.start(),
            method=m.group(1).upper(),
            confidence=_AMBIGUOUS_RECEIVER_CONFIDENCE,
        )


def laravel_http_calls(content: str) -> Iterator[ClientCallMatch]:
    """``Http::get($url)`` and the chained builders that end in a verb call."""
    if "Http::" not in content:
        return
    for m in _HTTP_FACADE_RE.finditer(content):
        pos = m.end()
        for _ in range(_MAX_CHAIN_LINKS):
            link = _CHAIN_LINK_RE.match(content, pos)
            if link is None:
                break
            open_idx = link.end() - 1
            close = match_paren(content, open_idx)
            if close < 0:
                break
            if link.group(1).lower() in VERBS:
                args = call_arguments(content, open_idx, close)
                if args:
                    yield ClientCallMatch(
                        client="laravel-http",
                        url=args[0],
                        offset=m.start(),
                        method=link.group(1).upper(),
                    )
                break
            arrow = _CHAIN_ARROW_RE.match(content, close + 1)
            if arrow is None:
                break  # the chain ended before naming a verb
            pos = arrow.end()


class PhpClientsDialect:
    name = "php-clients"
    extensions = PHP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        request_rows = list(guzzle_request_calls(ctx.content))
        verb_rows = list(guzzle_verb_calls(ctx.content))
        facade_rows = list(laravel_http_calls(ctx.content))
        if not request_rows and not verb_rows and not facade_rows:
            return []
        constants = string_constants(ctx.content, PHP_SYNTAX)
        # A Guzzle client composes a relative path onto its `base_uri`, so only
        # a rooted or absolute URL names the route reached.
        out = consumer_contracts(
            ctx,
            request_rows,
            PHP_SYNTAX,
            constants=constants,
            rooted_only=True,
        )
        # `->get('key')` on anything is a lookup, and a bare host carries no
        # route, so both verb-named shapes need a path.
        out += consumer_contracts(
            ctx,
            verb_rows,
            PHP_SYNTAX,
            constants=constants,
            path_only=True,
            rooted_only=True,
        )
        out += consumer_contracts(
            ctx,
            facade_rows,
            PHP_SYNTAX,
            constants=constants,
            path_only=True,
            rooted_only=True,
        )
        return out

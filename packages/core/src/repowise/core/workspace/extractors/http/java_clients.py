"""Java HTTP consumer dialect.

Three call shapes, each with its own source for the verb:

* Feign declarative clients: ``@RequestLine("POST /v1/{entityType}/images")``.
  Verb and path both sit inside one annotation string and the placeholders are
  already ``{param}`` shaped, so this is the cleanest Java shape there is.
* ``java.net.http``: ``HttpRequest.newBuilder(URI.create(url))`` or
  ``HttpRequest.newBuilder().uri(URI.create(url))``, with the verb read from the
  builder terminal (``.GET()``, ``.POST(...)``, ``.method("PATCH", ...)``) found
  by walking the chain up to its ``;`` or its ``.build()``.
* Spring ``RestTemplate``: ``getForObject`` / ``postForEntity`` and friends name
  the verb in the callee, ``exchange(uri, HttpMethod.GET, ...)`` names it in the
  second argument.

A chain with no terminal verb is refused rather than defaulted to GET, and a URL
that is a bare variable is refused unless the file assigns it a string literal
exactly once. OkHttp is left out: the corpus builds its ``Request.Builder`` from
a URL validated upstream, so no site carries a path this layer could read.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import JAVA
from .client_calls import (
    JAVA_SYNTAX,
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

# @RequestLine("POST /v1/{entityType}/images"): verb then path, one literal.
_REQUEST_LINE_RE = re.compile(r"@RequestLine\s*\(\s*\"([A-Za-z]+)[ \t]+([^\"\n]*)\"")

# The head of a java.net.http builder chain.
_NEW_BUILDER_RE = re.compile(r"\bHttpRequest\s*\.\s*newBuilder\s*\(")

# One `.name(` step of a fluent chain.
_CHAIN_STEP_RE = re.compile(r"\.\s*([A-Za-z_]\w*)\s*\(")

# How far past the chain head a terminal may sit. A java.net.http request is
# built in a handful of lines; beyond that the scan is reading unrelated code.
_CHAIN_LIMIT = 2000

# RestTemplate calls. The verb-suffixed names are unmistakable; bare `put` and
# `delete` are not, which is why the file is gated on the class name and the
# rows go through `path_only`.
_REST_TEMPLATE_RE = re.compile(
    r"\b(?:[A-Za-z_]\w*\s*\.\s*"
    r"(getForObject|getForEntity|postForObject|postForEntity|patchForObject|exchange)"
    r"|\w*[Tt]emplate\w*\s*\.\s*(put|delete))"
    r"\s*\("
)

_FEIGN_CONFIDENCE = 0.75
_BUILDER_CONFIDENCE = 0.75
_REST_TEMPLATE_CONFIDENCE = 0.65


def feign_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _REQUEST_LINE_RE.finditer(content):
        verb = m.group(1)
        if verb.lower() not in VERBS:
            continue
        yield ClientCallMatch(
            client="feign",
            # The annotation holds the path unquoted; the resolver reads a
            # literal, so hand it one.
            url=f'"{m.group(2).strip()}"',
            offset=m.start(),
            method=verb.upper(),
            confidence=_FEIGN_CONFIDENCE,
        )


def _chain_steps(content: str, start: int) -> Iterator[tuple[str, int]]:
    """``(step name, its '(' offset)`` for each ``.name(...)`` from *start*.

    Stops at the first character that is not part of the chain (the statement's
    ``;``, a comma, a closing paren) and after ``.build()``, so a verb belonging
    to a neighbouring call is never read as this request's terminal.
    """
    i = start
    limit = min(len(content), start + _CHAIN_LIMIT)
    while i < limit:
        if content[i].isspace():
            i += 1
            continue
        m = _CHAIN_STEP_RE.match(content, i)
        if m is None:
            return
        close = match_paren(content, m.end() - 1)
        if close < 0:
            return
        yield m.group(1), m.end() - 1
        if m.group(1) == "build":
            return
        i = close + 1


def java_net_http_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _NEW_BUILDER_RE.finditer(content):
        paren = m.end() - 1
        close = match_paren(content, paren)
        args = call_arguments(content, paren, close) if close >= 0 else None
        if args is None:
            continue
        # newBuilder(uri); the two-argument copy form carries a request, not a URL.
        url = args[0] if len(args) == 1 else None
        method: str | None = None
        for name, step_paren in _chain_steps(content, close + 1):
            if name == "uri" and url is None:
                inner = call_arguments(content, step_paren)
                if inner is not None and len(inner) == 1:
                    url = inner[0]
            elif name.isupper() and name.lower() in VERBS:
                method = name
            elif name == "method":
                inner = call_arguments(content, step_paren)
                if inner:
                    method = method_from_argument(inner[0])
        if url is None or method is None:
            continue  # no URL, or a verb only the caller knows
        yield ClientCallMatch(
            client="java-net-http",
            url=url,
            offset=m.start(),
            method=method,
            confidence=_BUILDER_CONFIDENCE,
        )


def resttemplate_calls(content: str) -> Iterator[ClientCallMatch]:
    if "RestTemplate" not in content:
        return
    for m in _REST_TEMPLATE_RE.finditer(content):
        callee = m.group(1) or m.group(2)
        paren = m.end() - 1
        args = call_arguments(content, paren)
        if not args:
            continue
        method = None
        if callee == "exchange":
            if len(args) < 2:
                continue
            method = method_from_argument(args[1])
            if method is None:
                continue  # a verb held in a variable is not one we know
        yield ClientCallMatch(
            client="resttemplate",
            url=args[0],
            offset=m.start(),
            callee=callee,
            method=method,
            confidence=_REST_TEMPLATE_CONFIDENCE,
        )


class JavaClientsDialect:
    name = "java-clients"
    extensions = JAVA

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        named = [*feign_calls(content), *java_net_http_calls(content)]
        # `.put`/`.delete` on any receiver could be a collection, so a URL with
        # no slash in it is a key rather than a route.
        ambiguous = list(resttemplate_calls(content))
        if not named and not ambiguous:
            return []
        constants = string_constants(content, JAVA_SYNTAX)
        return [
            *consumer_contracts(ctx, named, JAVA_SYNTAX, constants=constants),
            *consumer_contracts(ctx, ambiguous, JAVA_SYNTAX, constants=constants, path_only=True),
        ]

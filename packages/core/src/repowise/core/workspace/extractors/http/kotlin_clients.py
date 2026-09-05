"""Kotlin HTTP consumer dialect: Ktor client calls.

Ktor spells one request in three ways, and all three are recognised here:

* a verb with a URL argument: ``client.get("$TEST_SERVER/echo")``,
  ``client.post(path)``, optionally followed by a configuration block;
* a verb with only a block, where the URL is built inside it:
  ``client.get { url(path = "/widget", port = port) }``,
  ``client.post { url("http://host/widget") }``,
  ``client.get { url { takeFrom(base) } }``;
* ``client.request(...)`` with the verb set in the block:
  ``client.request("/echo") { method = HttpMethod.Post }``.

A block with no ``url(...)`` in it configures a request whose URL comes from
the client's ``defaultRequest``, which this file cannot see, so it is refused.
So is a ``request`` call with no ``method``, and the four-argument
``url(scheme, host, port, path)`` overload, whose path is positional and
indistinguishable from its scheme.

The receiver's type is unknown here: ``.get(...)`` is also how a map is read.
Three cheap gates stand in for it. The file has to name Ktor's client package,
the URL has to hold a ``/``, and the receiver may not be a type name, which is
what keeps a lookup key and a ``Paths.get`` from becoming routes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import KOTLIN
from .client_calls import (
    KOTLIN_SYNTAX,
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

# Ktor's client package. A file that never names it is not a Ktor call site,
# whether it imports the package or declares it.
_KTOR_PACKAGE = "io.ktor.client"

_VERBS = r"get|post|put|patch|delete|head"

# client.get("/x"): a verb with an argument list.
_VERB_ARG_RE = re.compile(rf"\.({_VERBS})\s*\(")
# client.get { ... }: a verb whose only argument is the configuration block.
_VERB_BLOCK_RE = re.compile(rf"\.({_VERBS})\s*\{{")
# client.request("/x") { ... } / client.request { ... }
_REQUEST_RE = re.compile(r"\.request\s*(\(|\{)")

# url("...") / url(path = "...") inside a request block.
_URL_CALL_RE = re.compile(r"\burl\s*\(")
# url { takeFrom("...") }: the URL built through a nested builder block.
_URL_BLOCK_RE = re.compile(r"\burl\s*\{")
_URL_BUILDER_CALL_RE = re.compile(r"\b(?:path|takeFrom)\s*\(")
# method = HttpMethod.Post, the verb a `request` block carries.
_METHOD_ASSIGN_RE = re.compile(r"\bmethod\s*=\s*([\w.]+)")

_NAMED_ARG_RE = re.compile(r"([A-Za-z_]\w*)\s*=(?!=)\s*(.*)", re.DOTALL)

# The identifier a call hangs off, when the receiver is a plain name.
_RECEIVER_RE = re.compile(r"[A-Za-z_]\w*$")

_CONFIDENCE = 0.65


def _block_end(content: str, open_idx: int) -> int:
    """Index of the ``}`` closing the lambda opened at *open_idx*, or -1."""
    return match_paren(content, open_idx, closer="}")


def _skip_site(content: str, offset: int) -> bool:
    """True when the call at *offset* cannot be a request on a client value.

    Two things a text scan can still tell apart. A KDoc example: Ktor documents
    every builder with one, so the same call shapes appear dozens of times in
    prose, and ``wrappers.mask_source`` covers Python and JS-like files only.
    And a receiver that is a type rather than a value: Kotlin names values in
    lower camel case, so ``Paths.get("build.gradle")`` is a file, not a route,
    while ``client.get(...)`` and ``createClient().get(...)`` both stay.
    """
    # Bounded so a long generated line is not copied once per call on it.
    line_start = max(content.rfind("\n", 0, offset) + 1, offset - 200)
    line = content[line_start:offset]
    if line.lstrip().startswith(("*", "//", "/*")):
        return True
    receiver = _RECEIVER_RE.search(line)
    return receiver is not None and receiver.group()[0].isupper()


def _url_argument(content: str, paren_offset: int) -> str | None:
    """The URL expression a ``url(...)`` call names, or ``None``.

    A named ``path`` argument wins; failing that the call has to carry exactly
    one positional argument, so the ``url(scheme, host, port, path)`` overload
    is refused rather than read as a URL string.
    """
    args = call_arguments(content, paren_offset)
    if not args:
        return None
    positional: list[str] = []
    for arg in args:
        named = _NAMED_ARG_RE.fullmatch(arg)
        if named is None:
            positional.append(arg)
        elif named.group(1) == "path":
            return named.group(2).strip()
    return positional[0] if len(positional) == 1 else None


def _block_url(content: str, block_start: int, block_end: int) -> str | None:
    """The URL a request block builds."""
    call = _URL_CALL_RE.search(content, block_start, block_end)
    if call is not None:
        return _url_argument(content, call.end() - 1)
    builder = _URL_BLOCK_RE.search(content, block_start, block_end)
    if builder is None:
        return None
    inner_end = _block_end(content, builder.end() - 1)
    if inner_end < 0:
        return None
    call = _URL_BUILDER_CALL_RE.search(content, builder.end(), inner_end)
    if call is None:
        return None
    args = call_arguments(content, call.end() - 1)
    return args[0] if args and len(args) == 1 else None


def _trailing_block(content: str, offset: int) -> int:
    """Index of the ``{`` opening a block right after *offset*, or -1."""
    i, n = offset, len(content)
    while i < n and content[i] in " \t\r\n":
        i += 1
    return i if i < n and content[i] == "{" else -1


def ktor_calls(content: str) -> Iterator[ClientCallMatch]:
    for m in _VERB_ARG_RE.finditer(content):
        if _skip_site(content, m.start()):
            continue
        args = call_arguments(content, m.end() - 1)
        if not args:
            continue
        yield ClientCallMatch(
            client="ktor",
            url=args[0],
            offset=m.start(),
            method=m.group(1).upper(),
            confidence=_CONFIDENCE,
        )

    for m in _VERB_BLOCK_RE.finditer(content):
        if _skip_site(content, m.start()):
            continue
        end = _block_end(content, m.end() - 1)
        if end < 0:
            continue
        url = _block_url(content, m.end(), end)
        if url is None:
            continue
        yield ClientCallMatch(
            client="ktor",
            url=url,
            offset=m.start(),
            method=m.group(1).upper(),
            confidence=_CONFIDENCE,
        )

    for m in _REQUEST_RE.finditer(content):
        if _skip_site(content, m.start()):
            continue
        if m.group(1) == "(":
            close = match_paren(content, m.end() - 1)
            if close < 0:
                continue
            args = call_arguments(content, m.end() - 1, close)
            url = args[0] if args else None
            block = _trailing_block(content, close + 1)
        else:
            url, block = None, m.end() - 1
        if block < 0:
            continue
        end = _block_end(content, block)
        if end < 0:
            continue
        if url is None:
            url = _block_url(content, block + 1, end)
            if url is None:
                continue
        verb = _METHOD_ASSIGN_RE.search(content, block, end)
        method = method_from_argument(verb.group(1)) if verb is not None else None
        if method is None:
            continue  # a request whose verb is not written here is not one we know
        yield ClientCallMatch(
            client="ktor",
            url=url,
            offset=m.start(),
            method=method,
            confidence=_CONFIDENCE,
        )


class KotlinClientsDialect:
    name = "kotlin-clients"
    extensions = KOTLIN

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if _KTOR_PACKAGE not in content:
            return []
        rows = list(ktor_calls(content))
        if not rows:
            return []
        # `.get("key")` on a map has no slash; the receiver is anyone's. A
        # relative path composes onto the client's default request base.
        return consumer_contracts(
            ctx,
            rows,
            KOTLIN_SYNTAX,
            constants=string_constants(content, KOTLIN_SYNTAX),
            path_only=True,
            rooted_only=True,
        )

"""Shared recognition layer for HTTP client calls.

The provider side splits recognition from output: ``framework_routes`` finds a
route registration once and each consumer of that match builds its own row.
This module is the consumer-side counterpart. A client library's call shape is
recognised in one function yielding :class:`ClientCallMatch` rows, and
:func:`consumer_contracts` turns those rows into contracts through one URL
resolver (:func:`..strings.resolve_string`), one method inference and one path
guard, so a dialect module is a table of recognisers and nothing else.

Placed under ``workspace`` because no graph-edge builder reads client calls;
it moves under ``ingestion`` the day one does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..base import line_at
from ..strings import StringSyntax, literal_span, resolve_string
from .dialect import build_consumer_contract, method_from_callee

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Every verb a call shape may name. Wider than ``dialect.METHODS``, which is
# the alternation the *callee-name* inference reads and is left as it is.
VERBS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})


@dataclass(frozen=True)
class ClientCallMatch:
    """One HTTP client call a recogniser found in a file's text.

    ``url`` is the URL argument's source text, unparsed; the strings layer's
    ``resolve_string`` reads it. ``method`` is the verb when the call shape settles it (a verb
    argument, a builder terminal, an explicit option) and ``None`` when only
    the callee's name carries it, in which case :func:`method_from_callee`
    reads ``callee``. ``offset`` is where the call starts, for its line.
    """

    client: str
    url: str
    offset: int
    callee: str = ""
    method: str | None = None
    confidence: float = 0.75


# ---------------------------------------------------------------------------
# Method inference from an argument
# ---------------------------------------------------------------------------

_QUOTED_WORD_RE = re.compile(r"""^['"]([A-Za-z]+)['"]$""")
_LAST_SEGMENT_RE = re.compile(r"(?:\.|::|->)")


def method_from_argument(text: str) -> str | None:
    """The HTTP verb an argument names, else ``None``.

    Reads a quoted verb (``'GET'``), and a verb constant by its last segment
    (``http.MethodGet``, ``HttpMethod.POST``, ``HTTPMethods.Get``,
    ``Net::HTTP::Get``). Anything else, a variable above all, is not a verb
    this layer may claim to know.
    """
    t = text.strip()
    m = _QUOTED_WORD_RE.match(t)
    if m is None:
        # A bare name is a verb only as a constant (`GET`), never as a
        # lower-case variable that happens to be called `get`.
        segments = _LAST_SEGMENT_RE.split(t)
        if len(segments) == 1 and not t.isupper():
            return None
    word = m.group(1) if m else _LAST_SEGMENT_RE.split(t)[-1]
    if not m and word.startswith("Method") and len(word) > len("Method"):
        word = word[len("Method") :]
    return word.upper() if word.lower() in VERBS else None


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

# A URL concrete enough to name a route on its own: a rooted path, a base
# placeholder, or an absolute URL. A relative path composes onto a base this
# layer cannot see.
_ROOTED_URL_RE = re.compile(r"^(?:/|\$\{|https?:|//)")


def is_rooted_url(url: str) -> bool:
    return _ROOTED_URL_RE.match(url) is not None


def consumer_contracts(
    ctx: ScanContext,
    matches: Iterable[ClientCallMatch],
    syntax: StringSyntax,
    *,
    constants: dict[str, str] | None = None,
    path_only: bool = False,
    rooted_only: bool = False,
) -> list[Contract]:
    """Consumer contracts for *matches*, resolving each URL through *syntax*.

    *path_only* drops a URL with no ``/`` in it. It is for recognisers whose
    receiver is ambiguous, so a map's ``.get("key")`` never becomes a route.

    *rooted_only* drops a URL that is neither absolute nor rooted. A client
    with a configured base composes ``"some/path"`` onto it, so the path the
    request reaches has a prefix this layer cannot see.
    """
    out: list[Contract] = []
    for m in matches:
        url = resolve_string(m.url, syntax, constants)
        if url is None or (path_only and "/" not in url):
            continue
        if rooted_only and not is_rooted_url(url):
            continue
        method = m.method or method_from_callee(m.callee)
        c = build_consumer_contract(
            ctx,
            method=method.upper(),
            url=url,
            client=m.client,
            line=line_at(ctx.content, m.offset),
            confidence=m.confidence,
        )
        if c is not None:
            out.append(c)
    return out


def matches_in(
    content: str,
    pattern: re.Pattern[str],
    *,
    client: str,
    url_group: int,
    method_group: int | None = None,
    callee_group: int | None = None,
    confidence: float = 0.75,
    prefix_group: int | None = None,
) -> Iterator[ClientCallMatch]:
    """Rows for every match of *pattern* whose *url_group* captured a literal body.

    The common recogniser: a regex that reads the callee (or a fixed client
    name), an optional verb group, and the body of a quoted URL literal.
    *prefix_group* is a literal prefix captured before the opening quote (C#'s
    ``$``/``@``), included in the row's ``url`` text.
    """
    for m in pattern.finditer(content):
        url = literal_span(content, m, url_group)
        if prefix_group is not None:
            url = m.group(prefix_group) + url
        yield ClientCallMatch(
            client=client,
            url=url,
            offset=m.start(),
            callee=m.group(callee_group) if callee_group is not None else "",
            method=m.group(method_group).upper() if method_group is not None else None,
            confidence=confidence,
        )


__all__ = [
    "VERBS",
    "ClientCallMatch",
    "consumer_contracts",
    "is_rooted_url",
    "matches_in",
    "method_from_argument",
]

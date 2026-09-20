"""HTTP dialect protocol and the shared contract builders.

A *dialect* is one framework's or client library's view of a source file. It
declares the file extensions it understands and turns raw regex matches into
:class:`Contract` instances via the two builders here, so every dialect emits
identically-shaped providers/consumers and the normalization rules live in one
place.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..base import ScanContext
from .paths import (
    absolute_host,
    consumer_meta,
    extract_path_from_url,
    is_unusable_consumer_path,
    normalize_http_path,
    strip_leading_base_expr,
    strip_trailing_query_expr,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

# Regex fragments for the HTTP method verbs, shared by every dialect's patterns.
METHODS = r"get|post|put|delete|patch"
METHODS_UPPER = r"GET|POST|PUT|DELETE|PATCH"

_VERB_TOKENS = frozenset(METHODS.split("|"))

# Identifier tokens, splitting camelCase and acronym runs: ``apiJSONPost`` ->
# ``api``, ``JSON``, ``Post``.
_CAMEL_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def method_from_callee(callee: str, default: str = "GET") -> str:
    """Read the HTTP verb a wrapper's *name* encodes, else *default*.

    ``apiPost`` -> ``POST``, ``apiDelete`` -> ``DELETE``. A leading verb wins
    outright so ``getPostById`` stays ``GET``; failing that, a single verb token
    names the method, and anything more ambiguous falls back rather than guess.
    """
    tokens = [t.lower() for t in _CAMEL_TOKEN_RE.findall(callee)]
    if tokens and tokens[0] in _VERB_TOKENS:
        return tokens[0].upper()
    verbs = [t for t in tokens if t in _VERB_TOKENS]
    return verbs[0].upper() if len(verbs) == 1 else default


@runtime_checkable
class HttpDialect(Protocol):
    """A framework/client recogniser for a set of file extensions."""

    name: str
    extensions: frozenset[str]

    def extract(self, ctx: ScanContext) -> list[Contract]:
        """Return the contracts found in *ctx* (may be empty)."""
        ...


def nearest_prefix(mappings: list[tuple[int, str]], pos: int) -> str:
    """Return the prefix of the nearest class mapping declared before *pos*.

    *mappings* is ``(start_offset, prefix)`` in ascending offset order (as
    produced by :func:`re.finditer`). Used by frameworks where a class-level
    route prefix (`@RequestMapping` / `[Route(...)]`) stitches onto each
    method-level route below it.
    """
    prefix = ""
    for cls_pos, cls_prefix in mappings:
        if cls_pos < pos:
            prefix = cls_prefix
        else:
            break
    return prefix


def build_provider_contract(
    ctx: ScanContext,
    *,
    method: str,
    path_raw: str,
    framework: str,
    line: int | None = None,
    confidence: float = 0.85,
    handler: str | None = None,
) -> Contract | None:
    """Build a provider contract, or ``None`` if the path is unusable.

    A match whose path normalizes to bare ``/`` only counts when the raw text
    actually carried a path — a template-variable-only or empty route is
    dropped, matching the legacy extractor's skip rule.

    *handler* is the expression the registration names, for frameworks that
    declare a route away from its handler (an ASP.NET minimal API). It is what
    lets :func:`.contracts.bind_symbol_ids` reach the handler rather than the
    registration site; without it the line lookup binds to ``Program.cs``.
    """
    from repowise.core.workspace.contracts import Contract

    norm_path = normalize_http_path(path_raw)
    if (not norm_path or norm_path == "/") and not path_raw.strip("/"):
        return None

    return Contract(
        repo=ctx.repo_alias,
        contract_id=f"http::{method}::{norm_path}",
        contract_type="http",
        role="provider",
        file_path=ctx.rel_path,
        symbol_name=f"{framework}:{method} {path_raw}",
        confidence=confidence,
        service=None,
        line=line,
        meta={
            "method": method,
            "path": norm_path,
            "framework": framework,
            **({"handler": handler} if handler else {}),
        },
    )


def build_consumer_contract(
    ctx: ScanContext,
    *,
    method: str,
    url: str,
    client: str,
    line: int | None = None,
    confidence: float = 0.75,
) -> Contract | None:
    """Build a consumer contract from a raw client-call URL.

    Returns ``None`` for URLs that can never be a meaningful match key — a
    truncated template literal or a path with no concrete segment (see
    :func:`is_unusable_consumer_path`).
    """
    from repowise.core.workspace.contracts import Contract

    host = absolute_host(url)
    path = extract_path_from_url(url)
    path, base_token = strip_leading_base_expr(path)
    path = strip_trailing_query_expr(path)
    norm_path = normalize_http_path(path)
    if is_unusable_consumer_path(norm_path):
        return None
    return Contract(
        repo=ctx.repo_alias,
        contract_id=f"http::{method}::{norm_path}",
        contract_type="http",
        role="consumer",
        file_path=ctx.rel_path,
        symbol_name=f"{client}:{method} {url}",
        confidence=confidence,
        service=None,
        line=line,
        meta=consumer_meta(method, norm_path, client, base_token, host),
    )

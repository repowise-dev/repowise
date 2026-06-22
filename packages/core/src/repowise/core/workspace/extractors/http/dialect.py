"""HTTP dialect protocol and the shared contract builders.

A *dialect* is one framework's or client library's view of a source file. It
declares the file extensions it understands and turns raw regex matches into
:class:`Contract` instances via the two builders here, so every dialect emits
identically-shaped providers/consumers and the normalization rules live in one
place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..base import ScanContext
from .paths import (
    absolute_host,
    consumer_meta,
    extract_path_from_url,
    is_unusable_consumer_path,
    normalize_http_path,
    strip_leading_base_expr,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

# Regex fragments for the HTTP method verbs, shared by every dialect's patterns.
METHODS = r"get|post|put|delete|patch"
METHODS_UPPER = r"GET|POST|PUT|DELETE|PATCH"


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
    confidence: float = 0.85,
) -> Contract | None:
    """Build a provider contract, or ``None`` if the path is unusable.

    A match whose path normalizes to bare ``/`` only counts when the raw text
    actually carried a path — a template-variable-only or empty route is
    dropped, matching the legacy extractor's skip rule.
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
        meta={"method": method, "path": norm_path, "framework": framework},
    )


def build_consumer_contract(
    ctx: ScanContext,
    *,
    method: str,
    url: str,
    client: str,
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
        meta=consumer_meta(method, norm_path, client, base_token, host),
    )

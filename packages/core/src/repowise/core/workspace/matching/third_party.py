"""Well-known third-party services a call reaches through a configured base URL.

A literal public host (``api.stripe.com``) already reads as third-party during
target resolution. A call written against a base variable has no host to read,
so ``${SUPABASE_URL}/rest/v1/todos`` would count as a call no workspace route
serves. A service family whose base variable and API paths are both distinctive
is declared here as one row, and such a call is marked third-party instead.

Both must hold: the base token names the service and the path starts with one of
the service's API prefixes. A target the service-name map or ``service_bases``
already placed in a workspace repo is left alone, so a self-hosted copy that is
a workspace member keeps its links. Adding a service is one row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from repowise.core.workspace.contracts import Contract


@dataclass(frozen=True)
class ThirdPartyService:
    """One service family, as data."""

    name: str
    #: Words one of which the base token holds, compared lower-cased with
    #: separators dropped, so ``SUPABASE_URL``, ``supabaseUrl`` and
    #: ``settings.supabase_url`` all read ``supabase``.
    base_words: tuple[str, ...]
    #: Path prefixes the service's API serves. A call must start with one.
    paths: tuple[str, ...]


#: One row per service family, sorted by name.
THIRD_PARTY_SERVICES: tuple[ThirdPartyService, ...] = (
    ThirdPartyService(
        "supabase",
        ("supabase",),
        ("/auth/v1/", "/rest/v1/", "/storage/v1/", "/functions/v1/", "/realtime/v1/"),
    ),
)

_SEPARATORS_RE = re.compile(r"[^a-z0-9]")


def third_party_service(base_token: str, path: str) -> str | None:
    """The service a call on *base_token* to *path* reaches, or None."""
    token = _SEPARATORS_RE.sub("", base_token.lower())
    for row in THIRD_PARTY_SERVICES:
        if any(w in token for w in row.base_words) and path.startswith(row.paths):
            return row.name
    return None


def mark_third_party_calls(contracts: Iterable[Contract]) -> None:
    """Mark each unplaced HTTP consumer a :data:`THIRD_PARTY_SERVICES` row names.

    Runs after :func:`.http.annotate_consumer_targets`, which clears and
    recomputes ``meta['external']`` every run, so this mark is recomputed too.
    """
    for c in contracts:
        if c.role != "consumer" or c.contract_type != "http":
            continue
        meta = c.meta
        token = meta.get("base_token")
        if not token or meta.get("external") or meta.get("target_repo"):
            continue
        if third_party_service(str(token), str(meta.get("path", ""))) is not None:
            meta["external"] = True

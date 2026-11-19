"""``package:<name>`` targets for get_context: where a third-party package is used.

Opt-in like ``component:`` and ``change:``; a default call never reads it. The
card is :func:`repowise.core.inventory.usage.package_usage` with its sites
capped, the rest recoverable through the omission store.
"""

from __future__ import annotations

from typing import Any

from repowise.core.inventory.usage import package_usages, parse_query
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection

PACKAGE_PREFIX = "package:"
_SITES_CAP = 25


def is_package_target(target: str) -> bool:
    return target.startswith(PACKAGE_PREFIX)


def _site_row(site: dict[str, Any]) -> dict[str, Any]:
    row = {k: site[k] for k in ("path", "line", "component", "mapping") if k in site}
    if site["symbols"]:
        row["symbols"] = site["symbols"]
    if site["owners"]:
        row["owners"] = site["owners"]
    if "owner_counts" in site:
        row["owner_counts"] = site["owner_counts"]
    row["reachability"] = site["reachability"]
    return row


async def package_cards(
    session: Any,
    repository: Any,
    targets: list[str],
    *,
    repo_root: Any,
    collector: OmissionCollector,
) -> dict[str, dict[str, Any]]:
    """Cards for ``package:<name>`` targets (a name or ``ecosystem:name``), one read for the batch."""
    cards: dict[str, dict[str, Any]] = {
        t: {"target": t, "error": "Name a package: package:<name> or package:<ecosystem>:<name>."}
        for t in targets
        if not t[len(PACKAGE_PREFIX) :].strip()
    }
    asked = [t for t in targets if t not in cards]
    if not asked:
        return cards
    queries = [parse_query(t[len(PACKAGE_PREFIX) :].strip()) for t in asked]
    docs = await package_usages(session, repository.id, repo_root, queries)
    for target, doc in zip(asked, docs, strict=True):
        card: dict[str, Any] = {"target": target, "type": "package", "package": doc["package"]}
        if "ecosystem" in doc:
            card["ecosystem"] = doc["ecosystem"]
        card["declared"] = doc["declared"]
        card["summary"] = doc["summary"]
        if doc["components"]:
            card["components"] = doc["components"]
        cap_collection(
            card,
            "sites",
            [_site_row(s) for s in doc["sites"]],
            _SITES_CAP,
            collector,
            label=f"{target} import sites beyond cap={_SITES_CAP}",
        )
        if "note" in doc:
            card["note"] = doc["note"]
        cards[target] = card
    return cards

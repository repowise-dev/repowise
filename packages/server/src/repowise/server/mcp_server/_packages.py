"""``package:<name>`` targets for get_context: where a third-party package is used.

Opt-in like ``component:`` and ``change:``; a default call never reads it. The
card is :func:`repowise.core.inventory.usage.package_usage` with its sites
capped, the rest recoverable through the omission store.

When an advisory document was imported (``repowise packages import``), the
card carries the package's advisories, each with its tier: how close
production code comes to it. ``package:<eco>:<name>@<version>`` adds a
``check`` of that version against the stored document. A package with no
stored advisory gets neither key.
"""

from __future__ import annotations

from typing import Any

from repowise.core.inventory.advisory_tiers import check_package, split_version, tier_advisories
from repowise.core.inventory.usage import lookup_key, package_usages, parse_query
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection

PACKAGE_PREFIX = "package:"
_SITES_CAP = 25
_ADVISORIES_CAP = 5


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
    split = [split_version(t[len(PACKAGE_PREFIX) :].strip()) for t in asked]
    queries = [parse_query(query) for query, _version in split]
    docs = await package_usages(session, repository.id, repo_root, queries)
    stored, tiered = await _advisories(session, repository.id, repo_root, queries)
    for target, doc, (name, eco), (_q, version) in zip(asked, docs, queries, split, strict=True):
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
        rows = [r for r in tiered if _same(r.ecosystem, r.package, eco, name)]
        if rows:
            cap_collection(
                card,
                "advisories",
                [_advisory_row(r) for r in rows],
                _ADVISORIES_CAP,
                collector,
                label=f"{target} advisories beyond cap={_ADVISORIES_CAP}",
            )
        if version is not None:
            check = check_package(stored, eco, name, version).as_dict()
            card["check"] = {k: v for k, v in check.items() if k not in ("package", "ecosystem")}
        cards[target] = card
    return cards


async def _advisories(
    session: Any, repo_id: str, repo_root: Any, queries: list[tuple[str, str | None]]
) -> tuple[Any, list[Any]]:
    """The stored document narrowed to the asked packages, and its tiered rows."""
    from repowise.core.inventory.advisories import load_advisories

    pairs = [(eco, name) for name, eco in queries]
    stored = await load_advisories(session, repo_id, packages=pairs)
    if stored is None or not any(p.advisories for p in stored.packages):
        return stored, []
    return stored, await tier_advisories(session, repo_id, repo_root, packages=pairs) or []


def _same(row_eco: str | None, row_name: str, eco: str | None, name: str) -> bool:
    if eco is not None and row_eco not in (None, eco):
        return False
    key_eco = eco or row_eco or ""
    return lookup_key(key_eco, row_name) == lookup_key(key_eco, name)


def _advisory_row(row: Any) -> dict[str, Any]:
    """``{id, tier, severity, fix, sites}``; the full record is in ``repowise packages vulns``."""
    out: dict[str, Any] = {"id": row.advisory.id, "tier": row.tier.tier}
    if row.advisory.severity:
        out["severity"] = row.advisory.severity
    if row.fix:
        out["fix"] = row.fix
    if row.affected is None:
        out["affected"] = None
    if sites := row.sites():
        out["sites"] = sites
    return out

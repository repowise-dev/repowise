"""The repository-level knowledge-risk summary behind the Contributors page.

One server-side reading, so the page never derives these figures from a
partial page of owners: the truck factor, single-owner files, silo modules,
how concentrated ownership is, who is active, and who has gone quiet while
still owning files that change. People only: automation owns nothing and is
counted separately.

Sync, no I/O, no clock. Reads the owner fold's accumulators and the same git
rows and identity resolver the fold was built from.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.analysis.owners import (
    ACTIVITY_WINDOW_DAYS,
    OwnerAccumulator,
    is_active,
    is_quiet,
)
from repowise.core.analysis.ownership_concentration import (
    OwnedFiles,
    module_silos,
    owned_files,
    top_owners,
    truck_factor,
)
from repowise.core.author_identity import IdentityResolver

#: Owners counted in the concentration figure.
CONCENTRATION_TOP_N = 3
#: Longest quiet-owner list returned; ``quiet_owners_total`` carries the rest.
QUIET_OWNERS_CAP = 25


def _iso(acc: OwnerAccumulator | None) -> str | None:
    at = acc.last_commit_at if acc else None
    return at.isoformat() if at else None


Accs = Mapping[str, OwnerAccumulator]


def _person(accs: Accs, key: str, resolve: IdentityResolver) -> dict:
    acc = accs.get(key)
    return {"key": key, "name": acc.name if acc else resolve.display_name(key)}


def _concentration(owned: OwnedFiles, accs: Accs, resolve: IdentityResolver) -> dict[str, Any]:
    total = owned.owned_total
    top = top_owners(owned, CONCENTRATION_TOP_N)
    return {
        "top_n": CONCENTRATION_TOP_N,
        "share": (sum(n for _, n in top) / total) if total else None,
        "owners": [
            {**_person(accs, key, resolve), "files_owned": n, "share": n / total}
            for key, n in top
        ],
    }


def _silos(owned: OwnedFiles, accs: Accs, resolve: IdentityResolver) -> list[dict[str, Any]]:
    return [
        {
            "module": s.module,
            "owner": _person(accs, s.owner, resolve),
            "owned_files": s.owned,
            "file_count": s.file_count,
            "share": s.share,
        }
        for s in module_silos(owned)
    ]


def _activity(people: list[OwnerAccumulator]) -> dict[str, Any]:
    """Who is active, and who has gone quiet while owning files that still change."""
    quiet = sorted(
        (a for a in people if is_quiet(a) and a.owned_changing_files > 0),
        key=lambda a: (-a.owned_changing_files, -a.files_owned, a.key),
    )
    return {
        "activity_window_days": ACTIVITY_WINDOW_DAYS,
        # A floor when the indexed commits stop inside the window.
        "active_contributors": sum(1 for a in people if is_active(a)),
        "activity_complete": any(a.commit_count_90d is not None for a in people),
        "quiet_owners": [
            {
                "key": a.key,
                "name": a.name,
                "last_commit_at": _iso(a),
                "files_owned": a.files_owned,
                "changing_files_owned": a.owned_changing_files,
            }
            for a in quiet[:QUIET_OWNERS_CAP]
        ],
        "quiet_owners_total": len(quiet),
    }


def build_owner_summary(
    git_rows: Iterable[Any],
    resolve: IdentityResolver,
    accs: Accs,
) -> dict[str, Any]:
    """The summary payload; see ``OwnersSummaryResponse`` for the fields."""
    owned = owned_files(git_rows, resolve)
    identities = [a for a in accs.values() if a.key]
    people = [a for a in identities if a.kind == "human"]
    return {
        "truck_factor": truck_factor(owned),
        "people": len(people),
        "automation_identities": len(identities) - len(people),
        "owners": len(owned.by_owner),
        "tracked_files": sum(owned.module_totals.values()),
        "owned_files": owned.owned_total,
        "single_owner_files": owned.single_owner_files,
        "single_owner_hotspots": owned.single_owner_hotspots,
        "concentration": _concentration(owned, accs, resolve),
        "silo_modules": _silos(owned, accs, resolve),
        **_activity(people),
    }


__all__ = ["CONCENTRATION_TOP_N", "QUIET_OWNERS_CAP", "build_owner_summary"]

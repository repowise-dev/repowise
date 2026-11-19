"""How concentrated a repository's file ownership is among people.

One reading of "who owns what", shared by the Stats page
(:func:`repowise.core.stats_highlights.build_people`) and the owners summary so
both publish the same truck factor, silos and single-owner count. Owners are
people: each file's primary owner goes through the caller's identity resolver
and automation (bots and coding agents) owns nothing here.

Sync, no I/O. Rows may be dicts, dataclasses or ORM rows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.health.aggregation import module_label
from repowise.core.analysis.health.rows import field as row_field
from repowise.core.author_identity import IdentityResolver

#: A module is a silo when one person owns more than this share of its files.
SILO_SHARE = 0.8


@dataclass
class OwnedFiles:
    """Primary-owned files per person, overall and per top-level module."""

    by_owner: Counter[str] = field(default_factory=Counter)
    by_module: dict[str, Counter[str]] = field(default_factory=dict)
    # Every file per module, owned by a person or not.
    module_totals: Counter[str] = field(default_factory=Counter)
    # Person-owned files whose bus factor is exactly 1 (0 means no history,
    # not risk), and those of them git flags as hotspots.
    single_owner_files: int = 0
    single_owner_hotspots: int = 0

    @property
    def owned_total(self) -> int:
        return sum(self.by_owner.values())


@dataclass(frozen=True)
class ModuleSilo:
    module: str
    owner: str
    owned: int
    file_count: int

    @property
    def share(self) -> float:
        return self.owned / self.file_count


def person_owner(row: Any, resolve: IdentityResolver) -> str | None:
    """The row's primary owner as a person's key; ``None`` for automation or nobody.

    Keyed as the owner fold keys it, so an email-only owner counts too.
    """
    name = row_field(row, "primary_owner_name")
    email = row_field(row, "primary_owner_email")
    if not (name or email):
        return None
    key = resolve(name, email)
    return key if key and not resolve.is_bot(key) else None


def owned_files(rows: Iterable[Any], resolve: IdentityResolver) -> OwnedFiles:
    """Fold git rows into per-person primary ownership."""
    out = OwnedFiles()
    for m in rows:
        owner = person_owner(m, resolve)
        module = module_label(row_field(m, "file_path"))
        out.module_totals[module] += 1
        if not owner:
            continue
        out.by_owner[owner] += 1
        out.by_module.setdefault(module, Counter())[owner] += 1
        if (row_field(m, "bus_factor") or 0) == 1:
            out.single_owner_files += 1
            out.single_owner_hotspots += bool(row_field(m, "is_hotspot"))
    return out


def truck_factor(owned: OwnedFiles) -> int | None:
    """Fewest people who together own more than half the owned files.

    ``None`` when no person owns a file.
    """
    total = owned.owned_total
    if not total:
        return None
    cumulative = count = 0
    for files in sorted(owned.by_owner.values(), reverse=True):
        cumulative += files
        count += 1
        if cumulative * 2 > total:
            break
    return count


def module_silos(owned: OwnedFiles) -> list[ModuleSilo]:
    """Modules where one person owns more than :data:`SILO_SHARE` of the files.

    Largest modules first, then by name.
    """
    silos = []
    for module, owners in owned.by_module.items():
        owner, files = min(owners.items(), key=lambda kv: (-kv[1], kv[0]))
        total = owned.module_totals[module]
        if files / total > SILO_SHARE:
            silos.append(ModuleSilo(module, owner, files, total))
    silos.sort(key=lambda s: (-s.file_count, s.module))
    return silos


def top_owners(owned: OwnedFiles, n: int) -> list[tuple[str, int]]:
    """The *n* people owning the most files, ties broken by key."""
    return sorted(owned.by_owner.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


__all__ = [
    "SILO_SHARE",
    "ModuleSilo",
    "OwnedFiles",
    "module_silos",
    "owned_files",
    "person_owner",
    "top_owners",
    "truck_factor",
]

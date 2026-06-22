"""The append-only registry of store-format migrations.

Each :class:`Migration` describes the upgrade impact of moving the store *to*
a given :data:`~repowise.core.upgrade.version.STORE_FORMAT_VERSION`. Adding a
future migration is a single appended entry — there is no control flow to edit,
which keeps the upgrade path scalable as the format evolves.

A migration applies to a store whose recorded version is ``< to_version`` and
whose target (the running build) is ``>= to_version``; :func:`migrations_between`
returns exactly those, in order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .verdict import UpgradeActionKind, UpgradeTier


@dataclass(frozen=True, slots=True)
class Migration:
    """One step in the store-format history.

    ``to_version``
        The store format version this entry introduces.
    ``tier``
        The upgrade impact (see :class:`UpgradeTier`).
    ``summary``
        One line shown to the user in "what's new" / upgrade notices.
    ``action``
        Optional auto action to run for ``AUTO``/``RE_PARSE`` tiers. ``None``
        for ``COMPATIBLE`` (nothing to do) and ``REINDEX_RECOMMENDED`` (never
        auto-run — we only inform).
    """

    to_version: int
    tier: UpgradeTier
    summary: str
    action: UpgradeActionKind | None = None


#: Ordered history of store-format migrations. APPEND ONLY — never reorder or
#: mutate shipped entries; older stores in the wild are assessed against this.
MIGRATIONS: tuple[Migration, ...] = (
    # v0 -> v1: the baseline. Legacy stores (no ``store_format_version`` field)
    # are version 0; reaching v1 needs nothing beyond the additive schema
    # reconcile that ``init_db`` already performs on every open.
    Migration(
        to_version=1,
        tier=UpgradeTier.COMPATIBLE,
        summary="Baseline store format (additive schema reconcile is automatic).",
        action=None,
    ),
)


def migrations_between(from_version: int, to_version: int) -> Sequence[Migration]:
    """Return migrations with ``from_version < to_version <= to_version``.

    Ordered ascending by ``to_version``. Empty when the store is already at or
    ahead of the target (e.g. a store written by a newer repowise opened by an
    older one — we never downgrade, just report nothing to do).
    """
    return tuple(m for m in MIGRATIONS if from_version < m.to_version <= to_version)


__all__ = ["MIGRATIONS", "Migration", "migrations_between"]

"""Git-indexing depth tiers.

``GitIndexTier`` is the public knob that lets large-repo callers trade
completeness for speed. ``FULL`` (the default) preserves the historical
behaviour exactly. ``ESSENTIAL`` runs only the cheap baseline — per-file
commit history with no per-file ``git blame`` and no co-change accumulation —
so the fast orchestrator path can index a 30k-file repo quickly and backfill
the expensive signals later (see :mod:`backfill`).
"""

from __future__ import annotations

import enum

__all__ = ["GitIndexTier"]


class GitIndexTier(enum.StrEnum):
    """Public knob for git-indexing depth on large repositories."""

    ESSENTIAL = "essential"
    FULL = "full"

    @property
    def includes_blame(self) -> bool:
        """Whether per-file ``git blame`` ownership runs in this tier.

        Blame is O(lines) and the dominant per-file cost; ESSENTIAL skips it
        and falls back to commit-author ownership.
        """
        return self is GitIndexTier.FULL

    @property
    def includes_co_change(self) -> bool:
        """Whether co-change pair accumulation runs in this tier.

        The repo-wide co-change walk is the other expensive signal; ESSENTIAL
        skips it and leaves ``co_change_partners_json`` empty until a FULL
        backfill fills it in.
        """
        return self is GitIndexTier.FULL

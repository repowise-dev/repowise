"""Value types describing the outcome of an upgrade assessment.

These are pure data: :class:`UpgradeManager` produces an :class:`UpgradeVerdict`
and never performs side effects itself. The CLI and server consume the verdict
as their single source of truth, so neither duplicates upgrade decision logic.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class UpgradeTier(enum.IntEnum):
    """Severity of what an upgrade requires, ordered low to high.

    Ordering matters: when several migrations apply across a version span, the
    overall tier is the maximum (most severe) of them.
    """

    #: Nothing to do. Additive schema reconcile (already automatic) covers it.
    COMPATIBLE = 0
    #: A no-LLM action runs automatically in-band (e.g. re-embed vectors).
    AUTO = 1
    #: The parse cache is rebuilt — cheap, automatic, happens on next ingest.
    RE_PARSE = 2
    #: Genuinely breaking. Never auto-run; surface a notice + exact command.
    REINDEX_RECOMMENDED = 3


class UpgradeActionKind(enum.StrEnum):
    """Auto-runnable actions, dispatched through an injected context.

    Keeping these as opaque kinds (rather than callables) keeps the core
    decision layer free of any dependency on the CLI provider stack — the
    executor that knows how to build embedders lives at the edge.
    """

    #: Re-embed all wiki pages + decisions via the existing reindex path.
    REEMBED_VECTORS = "reembed_vectors"
    #: Drop the parse cache so the next ingest re-parses from source.
    DROP_PARSE_CACHE = "drop_parse_cache"


@dataclass(frozen=True, slots=True)
class UpgradeAction:
    """One auto action to run, with a human-readable reason."""

    kind: UpgradeActionKind
    reason: str


@dataclass(frozen=True, slots=True)
class UpgradeVerdict:
    """The full assessment of upgrading a store to the running version.

    ``tier`` is the headline. ``actions`` are the no-LLM steps an executor
    should run for ``AUTO``/``RE_PARSE`` tiers. ``user_notice`` and
    ``reindex_command`` are populated for anything the user should see (most
    importantly the ``REINDEX_RECOMMENDED`` case, which we only ever inform).
    """

    tier: UpgradeTier
    from_store_version: int
    to_store_version: int
    written_by: str | None = None
    actions: tuple[UpgradeAction, ...] = field(default_factory=tuple)
    user_notice: str | None = None
    reindex_command: str | None = None
    #: Concise per-version highlights, filled by the presenter layer (Phase 2).
    summaries: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_noop(self) -> bool:
        """True when the upgrade needs nothing and warrants no message."""
        return self.tier == UpgradeTier.COMPATIBLE and not self.actions and not self.user_notice

    @property
    def reindex_recommended(self) -> bool:
        return self.tier == UpgradeTier.REINDEX_RECOMMENDED


__all__ = [
    "UpgradeAction",
    "UpgradeActionKind",
    "UpgradeTier",
    "UpgradeVerdict",
]

"""The single decision point for store upgrades.

:func:`assess` is pure: given the persisted ``state`` mapping and the running
build's versions, it returns an :class:`UpgradeVerdict`. It performs no I/O and
no side effects, so it is trivially unit-testable and identical whether called
from the CLI or the server.

:func:`apply_auto` runs the no-LLM auto actions a verdict carries, dispatching
through an injected :class:`UpgradeContext`. The context lives at the edge (the
CLI knows how to build embedders and find the parse cache); core stays free of
those dependencies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import structlog

from .registry import migrations_between
from .verdict import (
    UpgradeAction,
    UpgradeActionKind,
    UpgradeTier,
    UpgradeVerdict,
)
from .version import (
    STORE_FORMAT_VERSION,
    STORE_FORMAT_VERSION_KEY,
    WRITTEN_BY_VERSION_KEY,
)

log = structlog.get_logger(__name__)


@runtime_checkable
class UpgradeContext(Protocol):
    """Edge-supplied executor for a verdict's auto actions.

    Each method is best-effort: an executor that cannot perform an action (or
    fails) should log and return without raising, so a routine ``update`` never
    breaks on an opportunistic upgrade step.
    """

    async def reembed_vectors(self) -> None:
        """Rebuild vector embeddings from existing wiki pages (no LLM)."""
        ...

    def drop_parse_cache(self) -> None:
        """Remove the parse cache so the next ingest re-parses from source."""
        ...


def assess(
    state: Mapping[str, object],
    *,
    current_store_version: int = STORE_FORMAT_VERSION,
    recorded_embedding_model: str | None = None,
    current_embedding_model: str | None = None,
    stored_vector_dim: int | None = None,
    current_vector_dim: int | None = None,
) -> UpgradeVerdict:
    """Decide what (if anything) upgrading this store requires.

    ``state`` is the parsed ``state.json``. Missing version fields mean a legacy
    store: ``store_format_version`` defaults to 0. The verdict's tier is the max
    severity across the migrations spanned, escalated by an embedding-model
    mismatch (which adds a no-LLM re-embed action).

    ``recorded_embedding_model`` is the model the existing vectors were built
    with (the pinned ``embedding_model`` in ``config.yaml``, its canonical home;
    callers pass it explicitly). ``current_embedding_model`` is what the running
    build resolves now; a difference triggers a re-embed.

    ``stored_vector_dim`` / ``current_vector_dim`` catch the same drift one
    level down, and are the check that actually fires in practice: the model
    names above both come from ``REPOWISE_EMBEDDING_MODEL``, which almost
    nobody sets, so both sides are usually ``None`` and the comparison no-ops.
    Vector width is not opt-in — the table on disk records what built it, and a
    width the running embedder cannot produce means every stored vector is
    unreadable. Callers pass ``None`` for either when it cannot be determined,
    which keeps the check quiet rather than guessing.
    """
    from_version = _coerce_int(state.get(STORE_FORMAT_VERSION_KEY), default=0)
    written_by = _coerce_str(state.get(WRITTEN_BY_VERSION_KEY))

    spanned = migrations_between(from_version, current_store_version)
    tier = UpgradeTier.COMPATIBLE
    actions: list[UpgradeAction] = []
    summaries: list[str] = []
    reindex_command: str | None = None
    notice_parts: list[str] = []

    for migration in spanned:
        tier = max(tier, migration.tier)
        summaries.append(migration.summary)
        if migration.tier == UpgradeTier.REINDEX_RECOMMENDED:
            reindex_command = "repowise init --force"
            notice_parts.append(migration.summary)
        elif migration.action is not None:
            actions.append(UpgradeAction(kind=migration.action, reason=migration.summary))

    # Embedding-model mismatch is orthogonal to the format version: a store can
    # be format-current yet hold vectors from a different embedding model (#426),
    # which silently degrades search. Re-embedding has no LLM cost, so it is an
    # AUTO action rather than a reindex recommendation.
    recorded_model = recorded_embedding_model
    if recorded_model and current_embedding_model and recorded_model != current_embedding_model:
        tier = max(tier, UpgradeTier.AUTO)
        actions.append(
            UpgradeAction(
                kind=UpgradeActionKind.REEMBED_VECTORS,
                reason=(
                    f"embedding model changed ({recorded_model} -> "
                    f"{current_embedding_model}); re-embedding for search parity"
                ),
            )
        )

    if (
        stored_vector_dim is not None
        and current_vector_dim is not None
        and stored_vector_dim != current_vector_dim
    ):
        tier = max(tier, UpgradeTier.AUTO)
        actions.append(
            UpgradeAction(
                kind=UpgradeActionKind.REEMBED_VECTORS,
                reason=(
                    f"embedder changed (vectors are {stored_vector_dim}-wide, the configured "
                    f"embedder produces {current_vector_dim}); re-embedding so search can read them"
                ),
            )
        )

    user_notice = " ".join(notice_parts) if notice_parts else None

    return UpgradeVerdict(
        tier=tier,
        from_store_version=from_version,
        to_store_version=current_store_version,
        written_by=written_by,
        actions=tuple(actions),
        user_notice=user_notice,
        reindex_command=reindex_command,
        summaries=tuple(summaries),
    )


async def apply_auto(verdict: UpgradeVerdict, ctx: UpgradeContext) -> list[UpgradeActionKind]:
    """Run the verdict's auto actions through *ctx*. Best-effort.

    Returns the kinds that ran successfully. Never raises: a failed auto action
    is logged and skipped so a routine update is never blocked by it. A
    ``REINDEX_RECOMMENDED`` verdict carries no actions, so nothing runs — the
    recommendation is surfaced by the presenter layer, never forced here.
    """
    ran: list[UpgradeActionKind] = []
    for action in verdict.actions:
        # Two independent checks can both conclude "re-embed" (a model-name
        # change and a vector-width change usually travel together). Running it
        # twice bills the embedding API twice for an identical result.
        if action.kind in ran:
            continue
        try:
            if action.kind == UpgradeActionKind.REEMBED_VECTORS:
                await ctx.reembed_vectors()
            elif action.kind == UpgradeActionKind.DROP_PARSE_CACHE:
                ctx.drop_parse_cache()
            else:  # pragma: no cover - defensive; new kinds add a branch
                log.debug("upgrade_action_unhandled", kind=str(action.kind))
                continue
            ran.append(action.kind)
            log.info("upgrade_auto_action", kind=str(action.kind), reason=action.reason)
        except Exception as exc:
            log.warning("upgrade_auto_action_failed", kind=str(action.kind), error=str(exc))
    return ran


def _auto_reachable_version(from_version: int, target: int) -> int:
    """Highest store-format version reachable without an un-run reindex.

    A routine persist advances the recorded version only across migrations it
    can satisfy automatically (``COMPATIBLE`` / ``AUTO`` / ``RE_PARSE``). A
    ``REINDEX_RECOMMENDED`` migration is a hard gate: the store stays just below
    it until a full re-index runs, so :func:`assess` keeps returning the notice
    on every routine ``update`` until the user actually re-indexes. Without this
    clamp the first persist after an upgrade would stamp the store current and
    silence a recommendation the store has not yet earned.
    """
    ceiling = from_version
    for migration in migrations_between(from_version, target):
        if migration.tier >= UpgradeTier.REINDEX_RECOMMENDED:
            break
        ceiling = migration.to_version
    return ceiling


def stamp(
    state: dict[str, object],
    *,
    package_version: str | None,
    full_index: bool = False,
) -> dict[str, object]:
    """Write the current store-format markers into *state* in place and return it.

    Called on every persist so the store always records the format and the build
    that wrote it. ``embedding_model`` is stamped separately by the persistence
    layer that knows the resolved embedder.

    ``full_index`` distinguishes a full-repo (re)generation, which genuinely
    brings the store to :data:`STORE_FORMAT_VERSION`, from a routine incremental
    persist, which does not. Only the former stamps the terminal version; the
    latter clamps at the first ``REINDEX_RECOMMENDED`` gate (see
    :func:`_auto_reachable_version`) so a reindex recommendation is never
    silenced by an ordinary ``update``.
    """
    if full_index:
        state[STORE_FORMAT_VERSION_KEY] = STORE_FORMAT_VERSION
    else:
        recorded = _coerce_int(state.get(STORE_FORMAT_VERSION_KEY), default=0)
        state[STORE_FORMAT_VERSION_KEY] = _auto_reachable_version(recorded, STORE_FORMAT_VERSION)
    if package_version:
        state[WRITTEN_BY_VERSION_KEY] = package_version
    return state


def _coerce_int(value: object, *, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


__all__ = ["UpgradeContext", "apply_auto", "assess", "stamp"]

"""Unit tests for the store-format upgrade decision layer.

``assess`` is pure, so these exercise the full decision matrix directly:
legacy stores, current stores, embedding-model drift, and the
recommend-never-force contract for a synthetic breaking migration. ``apply_auto``
is verified to dispatch through an injected context and to never raise.
"""

from __future__ import annotations

import pytest

from repowise.core.upgrade import (
    STORE_FORMAT_VERSION,
    UpgradeAction,
    UpgradeActionKind,
    UpgradeTier,
    UpgradeVerdict,
    apply_auto,
    assess,
    stamp,
)
from repowise.core.upgrade import registry as registry_mod
from repowise.core.upgrade.registry import Migration, migrations_between

# --- assess: version-span tiers ------------------------------------------


def test_legacy_store_is_compatible_noop():
    """A store with no version fields (version 0) reaching v1 needs nothing."""
    verdict = assess({})
    assert verdict.from_store_version == 0
    assert verdict.to_store_version == STORE_FORMAT_VERSION
    assert verdict.tier == UpgradeTier.COMPATIBLE
    assert verdict.is_noop
    assert not verdict.actions


def test_current_store_is_noop():
    verdict = assess({"store_format_version": STORE_FORMAT_VERSION, "written_by_version": "0.21.0"})
    assert verdict.is_noop
    assert verdict.written_by == "0.21.0"


def test_store_from_future_version_is_noop():
    """A store written by a newer build (we never downgrade) reports nothing."""
    verdict = assess({"store_format_version": STORE_FORMAT_VERSION + 5})
    assert verdict.is_noop
    assert verdict.tier == UpgradeTier.COMPATIBLE


# --- assess: embedding-model drift ---------------------------------------


def test_embedding_model_change_triggers_auto_reembed():
    verdict = assess(
        {"store_format_version": STORE_FORMAT_VERSION},
        recorded_embedding_model="text-embedding-3-large",
        current_embedding_model="text-embedding-3-small",
    )
    assert verdict.tier == UpgradeTier.AUTO
    assert not verdict.is_noop
    kinds = [a.kind for a in verdict.actions]
    assert UpgradeActionKind.REEMBED_VECTORS in kinds


def test_same_embedding_model_is_noop():
    verdict = assess(
        {"store_format_version": STORE_FORMAT_VERSION},
        recorded_embedding_model="text-embedding-3-large",
        current_embedding_model="text-embedding-3-large",
    )
    assert verdict.is_noop


def test_missing_current_embedding_model_does_not_falsely_trigger():
    """When the running build resolves no model (env unset), do not re-embed."""
    verdict = assess(
        {"store_format_version": STORE_FORMAT_VERSION},
        recorded_embedding_model="text-embedding-3-large",
        current_embedding_model=None,
    )
    assert verdict.is_noop


def test_no_recorded_model_means_no_embedding_check():
    """With no pinned model to compare against, never re-embed."""
    verdict = assess(
        {"store_format_version": STORE_FORMAT_VERSION},
        recorded_embedding_model=None,
        current_embedding_model="model-b",
    )
    assert verdict.is_noop


# --- assess: recommend-never-force for breaking migrations ----------------


def test_breaking_migration_recommends_reindex_without_actions(monkeypatch):
    """A REINDEX_RECOMMENDED migration informs only; it carries no auto action."""
    fake = (
        Migration(1, UpgradeTier.COMPATIBLE, "baseline"),
        Migration(2, UpgradeTier.REINDEX_RECOMMENDED, "graph layout changed"),
    )
    monkeypatch.setattr(registry_mod, "MIGRATIONS", fake)
    verdict = assess({"store_format_version": 1}, current_store_version=2)
    assert verdict.tier == UpgradeTier.REINDEX_RECOMMENDED
    assert verdict.reindex_recommended
    assert verdict.reindex_command  # a command is surfaced
    assert verdict.user_notice
    assert not verdict.actions  # never auto-run


def test_tier_is_max_across_spanned_migrations(monkeypatch):
    fake = (
        Migration(1, UpgradeTier.COMPATIBLE, "baseline"),
        Migration(2, UpgradeTier.AUTO, "auto step", UpgradeActionKind.DROP_PARSE_CACHE),
        Migration(3, UpgradeTier.RE_PARSE, "reparse step", UpgradeActionKind.DROP_PARSE_CACHE),
    )
    monkeypatch.setattr(registry_mod, "MIGRATIONS", fake)
    verdict = assess({"store_format_version": 1}, current_store_version=3)
    assert verdict.tier == UpgradeTier.RE_PARSE
    assert len(verdict.summaries) == 2  # the two spanned, not the baseline


# --- registry helper ------------------------------------------------------


def test_migrations_between_is_exclusive_inclusive(monkeypatch):
    fake = tuple(Migration(v, UpgradeTier.COMPATIBLE, f"v{v}") for v in (1, 2, 3))
    monkeypatch.setattr(registry_mod, "MIGRATIONS", fake)
    spanned = migrations_between(1, 3)
    assert [m.to_version for m in spanned] == [2, 3]


# --- stamp ----------------------------------------------------------------


def test_stamp_writes_version_markers():
    state: dict[str, object] = {}
    stamp(state, package_version="9.9.9")
    assert state["store_format_version"] == STORE_FORMAT_VERSION
    assert state["written_by_version"] == "9.9.9"


def test_stamp_without_package_version_omits_written_by():
    state: dict[str, object] = {}
    stamp(state, package_version=None)
    assert state["store_format_version"] == STORE_FORMAT_VERSION
    assert "written_by_version" not in state


# --- apply_auto -----------------------------------------------------------


class _RecordingContext:
    def __init__(self, fail: bool = False) -> None:
        self.reembedded = False
        self.dropped = False
        self._fail = fail

    async def reembed_vectors(self) -> None:
        if self._fail:
            raise RuntimeError("boom")
        self.reembedded = True

    def drop_parse_cache(self) -> None:
        self.dropped = True


@pytest.mark.asyncio
async def test_apply_auto_dispatches_actions():
    verdict = UpgradeVerdict(
        tier=UpgradeTier.AUTO,
        from_store_version=0,
        to_store_version=1,
        actions=(
            UpgradeAction(UpgradeActionKind.REEMBED_VECTORS, "embedding changed"),
            UpgradeAction(UpgradeActionKind.DROP_PARSE_CACHE, "parser changed"),
        ),
    )
    ctx = _RecordingContext()
    ran = await apply_auto(verdict, ctx)
    assert ctx.reembedded and ctx.dropped
    assert set(ran) == {UpgradeActionKind.REEMBED_VECTORS, UpgradeActionKind.DROP_PARSE_CACHE}


@pytest.mark.asyncio
async def test_apply_auto_never_raises_on_failure():
    verdict = UpgradeVerdict(
        tier=UpgradeTier.AUTO,
        from_store_version=0,
        to_store_version=1,
        actions=(UpgradeAction(UpgradeActionKind.REEMBED_VECTORS, "x"),),
    )
    ctx = _RecordingContext(fail=True)
    ran = await apply_auto(verdict, ctx)  # must not raise
    assert ran == []

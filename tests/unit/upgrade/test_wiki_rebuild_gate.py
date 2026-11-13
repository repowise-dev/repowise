"""A ``REBUILD_WIKI`` migration stays owed until an update records the rebuild."""

from __future__ import annotations

import pytest

from repowise.core.upgrade import (
    Migration,
    UpgradeActionKind,
    UpgradeTier,
    apply_auto,
    assess,
    mark_wiki_rebuilt,
    pending_wiki_rebuilds,
    stamp,
)
from repowise.core.upgrade import manager as mgr
from repowise.core.upgrade import registry as reg

_REBUILD = Migration(3, UpgradeTier.AUTO, "wiki reorganized", UpgradeActionKind.REBUILD_WIKI)


@pytest.fixture
def with_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    migrations = (
        Migration(1, UpgradeTier.COMPATIBLE, "baseline"),
        Migration(2, UpgradeTier.REINDEX_RECOMMENDED, "concept tree"),
        _REBUILD,
    )
    monkeypatch.setattr(reg, "MIGRATIONS", migrations)
    monkeypatch.setattr(mgr, "STORE_FORMAT_VERSION", 3)


def _assess(state: dict):
    # ``assess`` binds the build's version as a default, so name the patched one.
    return assess(state, current_store_version=3)


def _store(version: int, docs_mode: str = "deterministic", **extra: object) -> dict:
    return {"store_format_version": version, "docs_mode": docs_mode, **extra}


def test_rebuild_is_owed_after_the_upgrade(with_rebuild: None) -> None:
    assert pending_wiki_rebuilds(_store(2)) == (_REBUILD,)
    verdict = _assess(_store(2))
    assert verdict.rebuilds_wiki
    assert verdict.tier == UpgradeTier.AUTO


def test_a_store_without_pages_owes_nothing(with_rebuild: None) -> None:
    assert pending_wiki_rebuilds(_store(2, docs_mode="none")) == ()
    assert not _assess(_store(2, docs_mode="none")).rebuilds_wiki
    # And a routine persist moves it straight past the migration.
    assert stamp(_store(2, docs_mode="none"), package_version=None)["store_format_version"] == 3


def test_routine_persist_holds_the_store_below_an_unrun_rebuild(with_rebuild: None) -> None:
    state = stamp(_store(2), package_version="0.60.0")
    assert state["store_format_version"] == 2
    assert pending_wiki_rebuilds(state)


def test_recorded_rebuild_lets_the_persist_advance(with_rebuild: None) -> None:
    state = _store(2)
    mark_wiki_rebuilt(state)
    stamp(state, package_version="0.60.0")
    assert state["store_format_version"] == 3
    assert pending_wiki_rebuilds(state) == ()
    assert _assess(state).is_noop


def test_rebuild_never_skips_a_reindex_gate(with_rebuild: None) -> None:
    state = _store(1)
    mark_wiki_rebuilt(state)
    stamp(state, package_version="0.60.0")
    assert state["store_format_version"] == 1
    assert _assess(state).reindex_recommended


def test_full_index_satisfies_everything(with_rebuild: None) -> None:
    state = stamp(_store(2), package_version="0.60.0", full_index=True)
    assert state["store_format_version"] == 3
    assert pending_wiki_rebuilds(state) == ()


async def test_apply_auto_leaves_the_rebuild_to_the_update(with_rebuild: None) -> None:
    class _Ctx:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def reembed_vectors(self) -> None:
            self.calls.append("reembed")

        def drop_parse_cache(self) -> None:
            self.calls.append("drop")

    ctx = _Ctx()
    ran = await apply_auto(_assess(_store(2)), ctx)
    assert ran == []
    assert ctx.calls == []

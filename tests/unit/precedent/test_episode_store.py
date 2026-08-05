"""Unit tests for the episode store (``repowise.core.precedent.store``)."""

from __future__ import annotations

from pathlib import Path

from repowise.core.precedent.store import (
    TIER_STRUCTURAL,
    Episode,
    EpisodeStore,
    default_store_path,
)


def _episode(kind: str = "nested_repos", subject: str = ".", body: str = "a") -> Episode:
    return Episode(
        tier=TIER_STRUCTURAL,
        kind=kind,
        subject=subject,
        body=body,
        evidence="e",
        nodes=("backend",),
    )


def _store(tmp_path: Path) -> EpisodeStore:
    (tmp_path / ".repowise").mkdir(exist_ok=True)
    return EpisodeStore.open_for_repo(tmp_path)


class TestReplaceKinds:
    def test_writes_and_reads_back(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            rows = store.list_episodes(tier=TIER_STRUCTURAL)
        assert len(rows) == 1
        assert rows[0]["kind"] == "nested_repos"
        assert rows[0]["nodes"] == ["backend"]

    def test_rederiving_preserves_birth(self, tmp_path: Path) -> None:
        """A fact that still holds keeps its birth; only the body may move."""
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos"],
                episodes=[_episode(body="first")],
                now=1000.0,
            )
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos"],
                episodes=[_episode(body="second")],
                now=2000.0,
            )
            rows = store.list_episodes()
        assert len(rows) == 1
        assert rows[0]["birth_at"] == 1000.0
        assert rows[0]["last_seen_at"] == 2000.0
        assert rows[0]["body"] == "second"

    def test_fact_that_stopped_holding_is_retired(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()], now=1000.0
            )
            retired = store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[], now=2000.0
            )
        assert retired == 1
        with _store(tmp_path) as store:
            assert store.count() == 0

    def test_replace_is_scoped_to_named_kinds(self, tmp_path: Path) -> None:
        """An update that skips the formatter check must not delete its episode."""
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos", "formatter_drift"],
                episodes=[_episode(), _episode(kind="formatter_drift", subject="ruff format .")],
                now=1000.0,
            )
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos"],
                episodes=[_episode()],
                now=2000.0,
            )
            kinds = {row["kind"] for row in store.list_episodes()}
        assert kinds == {"nested_repos", "formatter_drift"}

    def test_no_kinds_is_a_no_op(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(tier=TIER_STRUCTURAL, kinds=[], episodes=[_episode()])
            assert store.count() == 0


class TestPrune:
    def test_ttl_drops_only_unseen_rows(self, tmp_path: Path) -> None:
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), ttl_days=0.0) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            # Written with a current timestamp, so a zero TTL must not evict it.
            assert store.count() == 1

    def test_row_cap_never_evicts_the_last_row(self, tmp_path: Path) -> None:
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), max_rows=0) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            assert store.count() == 1


class TestPaths:
    def test_store_path_is_a_sidecar(self, tmp_path: Path) -> None:
        path = default_store_path(tmp_path)
        assert path.parent.parent.name == ".repowise"
        assert path.name == "episodes.db"
        assert not path.exists()  # resolving must not create anything

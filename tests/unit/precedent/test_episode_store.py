"""Unit tests for the episode store (``repowise.core.precedent.store``)."""

from __future__ import annotations

from pathlib import Path

from repowise.core.precedent.store import (
    TIER_GIT,
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

    def test_an_accumulating_tier_cannot_evict_the_cold_start_one(self, tmp_path: Path) -> None:
        """The cap is per tier.

        Structural facts are derived in the traverse phase, so a later
        tier-wide write leaves them holding the oldest ``last_seen_at`` in the
        store. Under a shared cap they would be first out of the door, and they
        are the only supply a first-ever index of a history-less repository
        has.
        """
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), max_rows=3) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()], now=1000.0
            )
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    Episode(
                        tier=TIER_GIT,
                        kind="code_fix",
                        subject=f"{i}",
                        body="b",
                        evidence="e",
                        nodes=("a.py",),
                        birth_at=float(i),
                    )
                    for i in range(10)
                ],
                oldest_birth_at=0.0,
                now=2000.0,
            )

            assert len(store.list_episodes(tier=TIER_STRUCTURAL)) == 1
            assert len(store.list_episodes(tier=TIER_GIT)) == 3

    def test_the_cap_evicts_the_oldest_claims_not_the_newest(self, tmp_path: Path) -> None:
        """One ``last_seen_at`` across a tier is a total tie.

        SQLite would fall back to insertion order, which is the walk's order,
        newest commit first. That would evict the most recent history from a
        layer whose subject is recent history.
        """
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), max_rows=2) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    Episode(
                        tier=TIER_GIT,
                        kind="code_fix",
                        subject=f"{i}",
                        body="b",
                        evidence="e",
                        nodes=("a.py",),
                        birth_at=float(i),
                    )
                    # Newest first, as the walk yields them.
                    for i in (5, 4, 3, 2, 1)
                ],
                oldest_birth_at=0.0,
            )

            kept = {row["birth_at"] for row in store.list_episodes(tier=TIER_GIT)}
            assert kept == {5.0, 4.0}

    def test_a_run_that_observed_nothing_does_not_refresh_the_tier(self, tmp_path: Path) -> None:
        """Without a trailing edge to vouch for, the touch is a false claim.

        A repository whose authors stop writing fix-shaped subjects yields no
        fixes on every later run. If those runs still marked the tier
        re-observed, the TTL would be answered by a pass that looked at no
        history and the rows would outlive every bound the store has.
        """
        (tmp_path / ".repowise").mkdir()
        episode = Episode(
            tier=TIER_GIT,
            kind="code_fix",
            subject="sha",
            body="b",
            evidence="e",
            nodes=("a.py",),
            birth_at=10.0,
        )
        with EpisodeStore(default_store_path(tmp_path), ttl_days=1.0) as store:
            store.append_tier(tier=TIER_GIT, episodes=[episode], oldest_birth_at=0.0, now=1000.0)

            store.append_tier(tier=TIER_GIT, episodes=[], oldest_birth_at=None, now=1060.0)
            assert [row["last_seen_at"] for row in store.list_episodes()] == [1000.0]

            store.prune(now=1000.0 + 2 * 86400)
            assert store.count() == 0


class TestPaths:
    def test_store_path_is_a_sidecar(self, tmp_path: Path) -> None:
        path = default_store_path(tmp_path)
        assert path.parent.parent.name == ".repowise"
        assert path.name == "episodes.db"
        assert not path.exists()  # resolving must not create anything

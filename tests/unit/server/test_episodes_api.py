"""/api/repos/{repo_id}/episodes — the HTTP surface over the sidecar store.

Three things these tests exist to hold, each of which is a defect the code
would otherwise be free to acquire quietly: the transcript tier never leaves
the machine, a repository with no store is a 200 rather than a 404 or a
crash, and a list route never shells out to git.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.precedent.store import (
    TIER_GIT,
    TIER_STRUCTURAL,
    TIER_TRANSCRIPT,
    Episode,
    EpisodeStore,
)

from .conftest import create_test_repo


def _episode(tier: str, kind: str, subject: str, birth_at: float, **kw) -> Episode:
    return Episode(
        tier=tier,
        kind=kind,
        subject=subject,
        body=f"the whole body of {subject}",
        evidence="evidence line",
        nodes=kw.get("nodes", ("pkg/a.py",)),
        birth_commit=kw.get("birth_commit", "b" * 40),
        birth_at=birth_at,
    )


def _seed(repo_dir: Path) -> None:
    """Two git episodes, one structural, one transcript that must stay local."""
    store = EpisodeStore.open_for_repo(repo_dir)
    store.append_tier(
        tier=TIER_GIT,
        episodes=[
            _episode(TIER_GIT, "code_fix", "sha1", 1000.0),
            _episode(TIER_GIT, "code_fix", "sha2", 1001.0, nodes=("pkg/b.py",)),
        ],
        oldest_birth_at=1000.0,
    )
    store.replace_kinds(
        tier=TIER_STRUCTURAL,
        kinds=["nested_repos"],
        episodes=[_episode(TIER_STRUCTURAL, "nested_repos", ".", 900.0)],
    )
    store.accumulate_tier(
        tier=TIER_TRANSCRIPT,
        kind="session",
        episodes=[_episode(TIER_TRANSCRIPT, "session", "secret-session", 1200.0)],
        present_subjects=["secret-session"],
    )
    store.close()


class TestColdStart:
    async def test_no_store_is_a_200_saying_so(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Not a 404: the repository exists, the feature has no data yet."""
        repo = await create_test_repo(client, tmp_path)
        resp = await client.get(f"/api/repos/{repo['id']}/episodes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["episodes"] == []
        assert body["total"] == 0

    async def test_reading_does_not_create_the_store(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Constructing EpisodeStore runs mkdir + the schema script."""
        repo = await create_test_repo(client, tmp_path)
        db = Path(repo["local_path"]) / ".repowise" / "episodes" / "episodes.db"
        for route in ("episodes", "episodes/counts", "episodes/by-file?path=pkg/a.py"):
            assert (await client.get(f"/api/repos/{repo['id']}/{route}")).status_code == 200
        assert not db.exists()
        assert not db.parent.exists()

    async def test_counts_degrade_the_same_way(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        body = (await client.get(f"/api/repos/{repo['id']}/episodes/counts")).json()
        assert body["available"] is False
        assert body["by_tier"] == {} and body["by_kind"] == {}

    async def test_unknown_repository_is_still_a_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Paired with a real repo, or a deleted router would pass this too."""
        assert (await client.get("/api/repos/nope/episodes")).status_code == 404
        repo = await create_test_repo(client, tmp_path)
        assert (await client.get(f"/api/repos/{repo['id']}/episodes")).status_code == 200


class TestTierSafety:
    async def test_the_transcript_tier_never_reaches_http(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Per-machine, so two people would otherwise see different pages."""
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        body = (await client.get(f"/api/repos/{repo['id']}/episodes")).json()
        assert body["total"] == 3
        assert {e["tier"] for e in body["episodes"]} == {TIER_GIT, TIER_STRUCTURAL}
        assert not any("secret-session" in e["subject"] for e in body["episodes"])

    async def test_a_transcript_episode_is_not_reachable_by_id(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        repo_dir = Path(repo["local_path"])
        _seed(repo_dir)
        store = EpisodeStore.open_for_repo(repo_dir)
        (secret,) = store.list_episodes(tier=TIER_TRANSCRIPT)
        store.close()
        resp = await client.get(f"/api/repos/{repo['id']}/episodes/{secret['id']}")
        assert resp.status_code == 404

    async def test_filtering_to_a_non_shareable_tier_returns_nothing(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """An unknown tier must select nothing, never fall through to all."""
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        for bad in (TIER_TRANSCRIPT, "made-up"):
            body = (
                await client.get(f"/api/repos/{repo['id']}/episodes?tier={bad}")
            ).json()
            assert body["episodes"] == [] and body["total"] == 0


class TestListing:
    async def test_rows_carry_no_body(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        body = (await client.get(f"/api/repos/{repo['id']}/episodes")).json()
        assert body["episodes"]
        assert all("body" not in e for e in body["episodes"])

    async def test_newest_first_and_pageable(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        base = f"/api/repos/{repo['id']}/episodes"
        every = (await client.get(f"{base}?limit=50")).json()["episodes"]
        assert [e["subject"] for e in every] == ["sha2", "sha1", "."]
        page = (await client.get(f"{base}?limit=1&offset=1")).json()
        assert [e["subject"] for e in page["episodes"]] == ["sha1"]
        # The total is measured, not the length of the window.
        assert page["total"] == 3

    async def test_scope_is_capped_with_the_real_count_beside_it(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """A cap without the count beside it is a silent lie about coverage."""
        from repowise.server.schemas.episodes import MAX_SUMMARY_NODES

        repo = await create_test_repo(client, tmp_path)
        repo_dir = Path(repo["local_path"])
        wide = tuple(f"pkg/f{i}.py" for i in range(MAX_SUMMARY_NODES + 8))
        store = EpisodeStore.open_for_repo(repo_dir)
        store.append_tier(
            tier=TIER_GIT,
            episodes=[_episode(TIER_GIT, "code_fix", "wide", 1000.0, nodes=wide)],
            oldest_birth_at=1000.0,
        )
        store.close()
        (row,) = (await client.get(f"/api/repos/{repo['id']}/episodes")).json()["episodes"]
        assert len(row["nodes"]) == MAX_SUMMARY_NODES
        assert row["node_count"] == len(wide)

    async def test_a_list_never_shells_out_to_git(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """~60 ms per row; a page of fifty would be three seconds of subprocess.

        Every route is asserted to have served *real rows*, not just a 200. A
        regression that quietly degraded all three to ``available: false``
        would satisfy "git was never called" while measuring nothing.
        """
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: pytest.fail("a list route asked git"),
        )
        for route in ("episodes", "episodes/by-file?path=pkg/a.py"):
            body = (await client.get(f"/api/repos/{repo['id']}/{route}")).json()
            assert body["available"] is True
            assert body["episodes"], f"{route} served nothing, so it proved nothing"
        counts = (await client.get(f"/api/repos/{repo['id']}/episodes/counts")).json()
        assert counts["available"] is True and counts["total"] == 3


class TestCounts:
    async def test_grouped_by_tier_and_kind(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        body = (await client.get(f"/api/repos/{repo['id']}/episodes/counts")).json()
        assert body["total"] == 3
        assert body["by_tier"] == {TIER_GIT: 2, TIER_STRUCTURAL: 1}
        assert body["by_kind"] == {"code_fix": 2, "nested_repos": 1}

    async def test_counts_is_not_read_as_an_episode_id(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """FastAPI matches in declaration order; below /{id} this would 404."""
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        resp = await client.get(f"/api/repos/{repo['id']}/episodes/counts")
        assert resp.status_code == 200
        assert "by_tier" in resp.json()


class TestByFile:
    async def test_returns_only_what_is_bound_there(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        body = (
            await client.get(
                f"/api/repos/{repo['id']}/episodes/by-file", params={"path": "pkg/b.py"}
            )
        ).json()
        assert [e["subject"] for e in body["episodes"]] == ["sha2"]
        assert body["total"] == 1

    async def test_a_path_with_nothing_bound_is_empty_but_available(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        body = (
            await client.get(
                f"/api/repos/{repo['id']}/episodes/by-file", params={"path": "pkg/zzz.py"}
            )
        ).json()
        assert body["available"] is True
        assert body["episodes"] == [] and body["total"] == 0

    async def test_path_is_required(self, client: AsyncClient, tmp_path: Path) -> None:
        repo = await create_test_repo(client, tmp_path)
        resp = await client.get(f"/api/repos/{repo['id']}/episodes/by-file")
        assert resp.status_code == 422


class TestDetail:
    async def test_serves_the_body_and_a_verdict(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        repo_dir = Path(repo["local_path"])
        _seed(repo_dir)
        listing = (await client.get(f"/api/repos/{repo['id']}/episodes")).json()
        one = listing["episodes"][0]
        body = (
            await client.get(f"/api/repos/{repo['id']}/episodes/{one['id']}")
        ).json()
        assert body["body"].startswith("the whole body of")
        assert body["subject"] == one["subject"]
        # git cannot answer in a bare tmp dir, so the verdict says so rather
        # than claiming currency. Both halves are always present.
        assert isinstance(body["current"], bool)
        assert body["still_true"]

    async def test_full_scope_is_not_capped_on_detail(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        from repowise.server.schemas.episodes import MAX_SUMMARY_NODES

        repo = await create_test_repo(client, tmp_path)
        repo_dir = Path(repo["local_path"])
        wide = tuple(f"pkg/f{i}.py" for i in range(MAX_SUMMARY_NODES + 8))
        store = EpisodeStore.open_for_repo(repo_dir)
        store.append_tier(
            tier=TIER_GIT,
            episodes=[_episode(TIER_GIT, "code_fix", "wide", 1000.0, nodes=wide)],
            oldest_birth_at=1000.0,
        )
        (row,) = store.list_episodes(tier=TIER_GIT)
        store.close()
        body = (
            await client.get(f"/api/repos/{repo['id']}/episodes/{row['id']}")
        ).json()
        assert len(body["nodes"]) == len(wide)

    async def test_unknown_episode_is_a_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Paired with a real id, or a deleted router would pass this too."""
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        real = (await client.get(f"/api/repos/{repo['id']}/episodes")).json()
        good = await client.get(
            f"/api/repos/{repo['id']}/episodes/{real['episodes'][0]['id']}"
        )
        assert good.status_code == 200
        bad = await client.get(f"/api/repos/{repo['id']}/episodes/deadbeef")
        assert bad.status_code == 404
        assert bad.json()["detail"] == "Episode not found"

    async def test_no_store_and_bad_id_are_told_apart(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Same status, different reason: one is worth a retry, one is not.

        The list routes draw this line with `available`; a single-object
        response has no room for it, so the wording carries it.
        """
        other = tmp_path / "other"
        other.mkdir()
        empty = await create_test_repo(client, tmp_path)
        seeded = await create_test_repo(client, other)
        _seed(Path(seeded["local_path"]))

        no_store = await client.get(f"/api/repos/{empty['id']}/episodes/deadbeef")
        bad_id = await client.get(f"/api/repos/{seeded['id']}/episodes/deadbeef")
        assert no_store.status_code == bad_id.status_code == 404
        assert no_store.json()["detail"] != bad_id.json()["detail"]
        assert "No episodes recorded" in no_store.json()["detail"]


class TestCoverageGaps:
    """Cases the first cut of this file asserted around rather than through."""

    async def test_a_valid_tier_filter_actually_filters(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        base = f"/api/repos/{repo['id']}/episodes"
        git_only = (await client.get(f"{base}?tier={TIER_GIT}")).json()
        structural = (await client.get(f"{base}?tier={TIER_STRUCTURAL}")).json()
        assert {e["tier"] for e in git_only["episodes"]} == {TIER_GIT}
        assert git_only["total"] == 2
        assert {e["tier"] for e in structural["episodes"]} == {TIER_STRUCTURAL}
        assert structural["total"] == 1

    async def test_a_kind_filter_actually_filters(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        base = f"/api/repos/{repo['id']}/episodes"
        body = (await client.get(f"{base}?kind=nested_repos")).json()
        assert [e["subject"] for e in body["episodes"]] == ["."]
        assert body["total"] == 1
        assert (await client.get(f"{base}?kind=no_such_kind")).json()["total"] == 0

    async def test_an_empty_tier_param_selects_nothing_not_everything(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """`?tier=` arrives as "", which must not read as "no filter"."""
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        body = (await client.get(f"/api/repos/{repo['id']}/episodes?tier=")).json()
        assert body["episodes"] == [] and body["total"] == 0

    async def test_a_repo_with_no_local_path_is_unavailable_not_a_crash(
        self, client: AsyncClient, tmp_path: Path, app
    ) -> None:
        """`local_path` is non-null in the model but empty rows predate that."""
        repo = await create_test_repo(client, tmp_path)
        async with app.state.session_factory() as session:
            from repowise.core.persistence import crud

            row = await crud.get_repository(session, repo["id"])
            row.local_path = ""
            await session.commit()
        for route in ("episodes", "episodes/counts", "episodes/by-file?path=a.py"):
            resp = await client.get(f"/api/repos/{repo['id']}/{route}")
            assert resp.status_code == 200
            assert resp.json()["available"] is False
        detail = await client.get(f"/api/repos/{repo['id']}/episodes/deadbeef")
        assert detail.status_code == 404

    async def test_detail_asks_git_exactly_once(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bounded at one, not merely "few" — the budget the docstring claims."""
        from repowise.core.precedent import currency as currency_mod

        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))
        calls: list[tuple] = []
        monkeypatch.setattr(
            currency_mod,
            "commits_since",
            lambda *a, **k: calls.append((a, k)) or 0,
        )
        listing = (await client.get(f"/api/repos/{repo['id']}/episodes")).json()
        git_row = next(e for e in listing["episodes"] if e["tier"] == TIER_GIT)
        resp = await client.get(f"/api/repos/{repo['id']}/episodes/{git_row['id']}")
        assert resp.status_code == 200
        assert len(calls) == 1

    async def test_a_failing_query_degrades_rather_than_500ing(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SQLITE_BUSY past the timeout — a dashboard read during an index write."""
        repo = await create_test_repo(client, tmp_path)
        _seed(Path(repo["local_path"]))

        def boom(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(EpisodeStore, "count_episodes", boom)
        monkeypatch.setattr(EpisodeStore, "group_counts", boom)
        monkeypatch.setattr(EpisodeStore, "count_by_node", boom)
        for route in ("episodes", "episodes/counts", "episodes/by-file?path=pkg/a.py"):
            resp = await client.get(f"/api/repos/{repo['id']}/{route}")
            assert resp.status_code == 200, route
            assert resp.json()["available"] is False

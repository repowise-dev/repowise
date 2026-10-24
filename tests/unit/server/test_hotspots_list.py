"""The hotspot list: scope, search, filters, sort, rank and facets."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.analysis.finding_registry import stored_origin
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import HealthFileMetric
from repowise.server.services.hotspots import (
    accelerating_clause,
    is_accelerating,
    origin_clause,
)
from tests.unit.server.conftest import create_test_repo

_FIXED = datetime(2026, 9, 1, tzinfo=UTC)

# path, score, commits 90d/30d, age, bus factor, owner, magnet, origin
_ROWS = [
    ("src/core.py", 9.0, 30, 20, 400, 1, "Ada", True, "production"),
    ("src/api.py", 7.0, 20, 2, 400, 2, "Grace", False, "production"),
    ("src/new.py", 6.0, 10, 10, 20, 1, "Ada", False, "production"),  # new, not accelerating
    ("tests/test_core.py", 8.0, 25, 15, 400, 0, "Linus", True, "test"),
    ("build/gen.py", 5.0, 5, 1, 400, 1, "Ada", False, "build"),
    ("src/legacy.py", 4.0, 4, 3, 900, 3, None, False, None),  # no health row
]


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for path, score, c90, c30, age, bf, owner, magnet, origin in _ROWS:
            await crud.upsert_git_metadata(
                session,
                repository_id=repo_id,
                file_path=path,
                commit_count_total=c90 * 2,
                commit_count_90d=c90,
                commit_count_30d=c30,
                age_days=age,
                bus_factor=bf,
                primary_owner_name=owner,
                is_hotspot=True,
                is_stable=False,
                churn_percentile=score / 10,
                temporal_hotspot_score=score,
                prior_defect_count=3 if magnet else 0,
                bug_magnet=magnet,
                last_fix_at=_FIXED if magnet else None,
            )
            if origin is not None:
                session.add(
                    HealthFileMetric(
                        repository_id=repo_id, file_path=path, nloc=10, code_origin=origin
                    )
                )
        # A file that is not a hotspot never lists.
        await crud.upsert_git_metadata(
            session, repository_id=repo_id, file_path="src/calm.py", is_hotspot=False
        )


async def _list(client: AsyncClient, repo_id: str, **params) -> dict:
    resp = await client.get(f"/api/repos/{repo_id}/hotspots", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _paths(payload: dict) -> list[str]:
    return [r["file_path"] for r in payload["items"]]


@pytest.mark.asyncio
async def test_default_lists_every_hotspot_by_score(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    page = await _list(client, repo["id"])
    assert _paths(page) == [
        "src/core.py",
        "tests/test_core.py",
        "src/api.py",
        "src/new.py",
        "build/gen.py",
        "src/legacy.py",
    ]
    assert [r["rank"] for r in page["items"]] == [1, 2, 3, 4, 5, 6]
    assert page["total"] == 6
    # Bus factor 0 is "no history", not one owner; the new file is not accelerating.
    assert page["facets"] == {
        "total": 6,
        "bus_factor_1": 3,
        "accelerating": 3,
        "bug_magnet": 2,
        "with_fixes": 2,
    }


@pytest.mark.asyncio
async def test_hold_map_leaves_out_held_origins_and_ranks_within_scope(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    page = await _list(client, repo["id"], hold="map")
    assert _paths(page) == ["src/core.py", "src/api.py", "src/new.py", "src/legacy.py"]
    assert [r["rank"] for r in page["items"]] == [1, 2, 3, 4]

    back = await _list(client, repo["id"], hold="map", include="TEST")
    assert "tests/test_core.py" in _paths(back)
    assert "build/gen.py" not in _paths(back)


@pytest.mark.asyncio
async def test_facets_sum_and_match_each_filters_total(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    base = await _list(client, repo["id"], hold="map", limit=500)
    facets = base["facets"]
    assert facets["total"] == base["total"] == len(base["items"])
    for name in ("bus_factor_1", "accelerating", "bug_magnet"):
        page = await _list(client, repo["id"], hold="map", filter=name, limit=500)
        assert page["total"] == facets[name] == len(page["items"])
        # The facets describe the searched set, not the filtered page.
        assert page["facets"] == facets
    flags = {r["file_path"]: r["accelerating"] for r in base["items"]}
    assert flags == {
        "src/core.py": True,
        "src/api.py": False,
        "src/new.py": False,
        "src/legacy.py": True,
    }


@pytest.mark.asyncio
async def test_search_matches_path_or_owner_and_keeps_global_rank(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    by_owner = await _list(client, repo["id"], q="grace")
    assert _paths(by_owner) == ["src/api.py"]
    assert by_owner["items"][0]["rank"] == 3
    assert by_owner["facets"]["total"] == 1

    by_path = await _list(client, repo["id"], q="LEGACY")
    assert _paths(by_path) == ["src/legacy.py"]

    # LIKE wildcards are literal.
    assert (await _list(client, repo["id"], q="%"))["total"] == 0


@pytest.mark.asyncio
async def test_sort_and_offset_page_through_the_whole_set(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    asc = await _list(client, repo["id"], sort="commits", order="asc")
    assert _paths(asc)[0] == "src/legacy.py"
    assert asc["items"][0]["rank"] == 6

    first = await _list(client, repo["id"], limit=4)
    rest = await _list(client, repo["id"], limit=4, offset=4)
    assert first["has_more"] is True and first["next_offset"] == 4
    assert rest["has_more"] is False and rest["next_offset"] is None
    assert len(set(_paths(first)) | set(_paths(rest))) == 6


@pytest.mark.asyncio
async def test_facets_follow_a_git_only_remine(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])
    assert (await _list(client, repo["id"]))["facets"]["total"] == 6

    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_metadata(
            session, repository_id=repo["id"], file_path="src/late.py", is_hotspot=True,
            commit_count_90d=1, commit_count_30d=0, churn_percentile=0.1,
        )
    page = await _list(client, repo["id"])
    assert page["facets"]["total"] == page["total"] == 7


@pytest.mark.asyncio
async def test_rejects_unknown_filter_and_sort(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    for params in ({"filter": "hot"}, {"sort": "trend"}, {"order": "up"}):
        resp = await client.get(f"/api/repos/{repo['id']}/hotspots", params=params)
        assert resp.status_code == 422


def test_accelerating_needs_a_prior_window() -> None:
    assert is_accelerating(5, 10, 400)
    assert not is_accelerating(3, 10, 400)
    assert not is_accelerating(10, 10, 30)
    assert not is_accelerating(None, None, None)
    assert accelerating_clause() is not None


@pytest.mark.parametrize(
    ("code_origin", "is_test"),
    [(None, None), (None, True), ("", True), ("", False), ("build", True), ("production", None)],
)
@pytest.mark.asyncio
async def test_origin_clause_matches_stored_origin(app, code_origin, is_test) -> None:
    from sqlalchemy import select

    async with get_session(app.state.session_factory) as session:
        row = HealthFileMetric(
            repository_id="r", file_path="x.py", nloc=1, code_origin=code_origin, is_test=is_test
        )
        session.add(row)
        await session.flush()
        got = await session.scalar(
            select(origin_clause()).where(HealthFileMetric.id == row.id)
        )
        await session.rollback()
    assert got == stored_origin(code_origin, is_test)

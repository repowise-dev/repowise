"""Plan lists read the rank finalize stored, and answer exactly as ranking live did.

The golden here is the live path itself: every query is answered once from the
stored rank with per-request ranking disabled, then again after the stored rank
is cleared, and the two responses must be identical.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.refactoring import _refactoring_row_kwargs
from repowise.core.persistence.models import RefactoringSuggestion
from repowise.server.routers import refactoring as router

from .test_refactoring import _seed

pytestmark = pytest.mark.usefixtures("dry_violation_shown")

_EXTRA = [
    {
        "refactoring_type": "extract_method",
        "file_path": "pkg/leaf.py",
        "target_symbol": "long_one",
        "line_start": 210,
        "line_end": 260,
        "plan": {"strategy": "lift the parsing loop"},
        "evidence": {},
        "impact_delta": 1.2,
        "effort_bucket": "S",
        "blast_radius": {},
        "confidence": "high",
        "source_biomarker": "large_method",
    },
    {
        "refactoring_type": "split_file",
        "file_path": "pkg/hub.py",
        "target_symbol": "pkg/hub.py",
        "plan": {"groups": [{"suggested_file": "pkg/hub_io.py"}]},
        "evidence": {"group_count": 2},
        "impact_delta": 0.3,
        "effort_bucket": "XL",
        "blast_radius": {"files": ["pkg/hub.py", "pkg/a.py", "pkg/b.py"]},
        "confidence": "low",
        "source_biomarker": "",
    },
    {
        "refactoring_type": "performance_fix",
        "file_path": "pkg/b.py",
        "target_symbol": "load_all",
        "line_start": 12,
        "line_end": 18,
        "plan": {"opportunity_id": "perf_x", "steps": []},
        "evidence": {"rank_factors": {"loop_magnitude": 2.0}},
        "impact_delta": 0.0,
        "effort_bucket": "M",
        "blast_radius": {"call_sites": 2},
        "confidence": "medium",
        "source_biomarker": "io_in_loop",
    },
]

_QUERIES = [
    ("targets", {}),
    ("targets", {"view": "file_spread"}),
    ("targets", {"refactoring_type": "extract_method"}),
    ("targets", {"min_confidence": "medium", "file_path": "pkg/leaf.py"}),
    ("targets/page", {}),
    ("targets/page", {"limit": 2, "offset": 1}),
    ("targets/page", {"view": "file_spread", "refactoring_type": "structural"}),
    ("targets/page", {"sort": "health"}),
    ("targets/page", {"sort": "effort", "view": "file_spread"}),
    ("targets/page", {"sort": "blast"}),
    ("targets/page", {"sort": "file", "limit": 3}),
    ("targets/page", {"search": "helper.do_work"}),
    ("targets/page", {"search": "parsing loop"}),
    ("targets/page", {"confidence": "medium,high", "effort": "S,M", "view": "file_spread"}),
    ("targets/page", {"min_confidence": "medium", "limit": 1, "offset": 2}),
]


async def _seed_ranked(client: AsyncClient, app) -> str:
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        for plan in _EXTRA:
            session.add(RefactoringSuggestion(**_refactoring_row_kwargs(plan, repo_id)))
        # Ranked at finalize, never listed: lists show open plans only.
        session.add(
            RefactoringSuggestion(
                **_refactoring_row_kwargs({**_EXTRA[0], "target_symbol": "picked_up"}, repo_id),
                status="acknowledged",
            )
        )
        await session.flush()
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
    return repo_id


async def _answers(client: AsyncClient, repo_id: str) -> list[dict]:
    out = []
    for path, params in _QUERIES:
        response = await client.get(f"/api/repos/{repo_id}/refactoring/{path}", params=params)
        assert response.status_code == 200, (path, params, response.text)
        out.append(response.json())
    return out


async def _unrank(app, repo_id: str, *, public_id: str | None = None) -> None:
    async with app.state.session_factory() as session:
        statement = update(RefactoringSuggestion).where(
            RefactoringSuggestion.repository_id == repo_id
        )
        if public_id is not None:
            statement = statement.where(RefactoringSuggestion.public_id == public_id)
        await session.execute(statement.values(rank_position=None, rank_json=None, blast_size=None))
        await session.commit()


def _no_live_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a ranked store must not be ranked per request")

    monkeypatch.setattr(router, "hydrate_recommendations", refuse)
    monkeypatch.setattr(router, "detail_recommendations", refuse)


async def test_stored_rank_answers_every_query_as_live_ranking_did(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _seed_ranked(client, app)
    with monkeypatch.context() as patch:
        _no_live_ranking(patch)
        stored = await _answers(client, repo_id)
    await _unrank(app, repo_id)
    live = await _answers(client, repo_id)

    assert stored == live
    # The rank order is the canonical one: a non-empty default page leads with
    # the highest score among production plans.
    page = stored[4]
    assert page["total"] == 7
    assert [item["id"] for item in page["items"]] == [plan["id"] for plan in stored[0]["plans"]]


async def test_a_plan_finalize_did_not_rank_sends_the_list_live(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _seed_ranked(client, app)
    expected = await _answers(client, repo_id)
    async with app.state.session_factory() as session:
        reopened = (
            await session.execute(
                select(RefactoringSuggestion.public_id).where(
                    RefactoringSuggestion.repository_id == repo_id,
                    RefactoringSuggestion.refactoring_type == "move_method",
                )
            )
        ).scalar_one()
    await _unrank(app, repo_id, public_id=reopened)

    calls: list[int] = []
    hydrate = router.hydrate_recommendations

    async def counting(*args, **kwargs):
        calls.append(1)
        return await hydrate(*args, **kwargs)

    monkeypatch.setattr(router, "hydrate_recommendations", counting)
    assert await _answers(client, repo_id) == expected
    assert calls


async def test_one_plan_reads_its_stored_rank(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _seed_ranked(client, app)
    listed = (await client.get(f"/api/repos/{repo_id}/refactoring/targets")).json()["plans"]
    _no_live_ranking(monkeypatch)
    for plan in listed:
        detail = (await client.get(f"/api/repos/{repo_id}/refactoring/{plan['id']}")).json()
        assert detail == plan


async def test_finalize_ranks_only_live_plans(client: AsyncClient, app) -> None:
    repo_id = await _seed_ranked(client, app)
    async with app.state.session_factory() as session:
        await crud.update_refactoring_suggestion_status(
            session, repo_id, (await _ids(session, repo_id))[0], "resolved"
        )
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
        rows = (
            await session.execute(
                select(RefactoringSuggestion).where(RefactoringSuggestion.repository_id == repo_id)
            )
        ).scalars()
        ranked: dict[str, list[int | None]] = {}
        for row in rows:
            ranked.setdefault(row.status, []).append(row.rank_position)
    assert ranked["resolved"] == [None]
    live = ranked["open"] + ranked["acknowledged"]
    assert sorted(live) == list(range(len(live)))


async def _ids(session, repo_id: str) -> list[str]:
    return list(
        (
            await session.execute(
                select(RefactoringSuggestion.id)
                .where(RefactoringSuggestion.repository_id == repo_id)
                .order_by(RefactoringSuggestion.file_path, RefactoringSuggestion.target_symbol)
            )
        ).scalars()
    )

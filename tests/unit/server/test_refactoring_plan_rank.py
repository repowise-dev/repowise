"""Plan lists read the rank finalize stored, and answer exactly as ranking live did.

The golden here is the live path itself: every query is answered once from the
stored rank with per-request ranking disabled, then again after the stored rank
is cleared, and the two responses must be identical.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from repowise.core.analysis.health.refactoring import llm, recommendations
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.refactoring import _refactoring_row_kwargs
from repowise.core.persistence.models import RefactoringSuggestion
from repowise.server.services import refactoring_health as service

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
    ("targets", {"view": "file_spread", "refactoring_type": "structural"}),
    ("targets", {"min_confidence": "medium", "view": "file_spread"}),
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

    monkeypatch.setattr(service, "hydrate_recommendations", refuse)
    # The one-plan rebuild from seeks is the fallback too.
    monkeypatch.setattr(recommendations, "build_recommendations", refuse)
    monkeypatch.setattr(service.RefactoringHealthService, "_rank_inputs", refuse)


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
    assert len(stored[0]["plans"]) == 7


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
    hydrate = service.hydrate_recommendations

    async def counting(*args, **kwargs):
        calls.append(1)
        return await hydrate(*args, **kwargs)

    monkeypatch.setattr(service, "hydrate_recommendations", counting)
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
        # Detail adds what other layers say about the target; no decision here.
        assert detail.pop("governed_by") == []
        assert all(risk["kind"] != "decision" for risk in detail.pop("risks"))
        assert detail == plan

    # Code generation reads its plan the same way.
    seen: list[dict] = []

    class _Generated:
        def __init__(self, suggestion) -> None:
            self.suggestion = suggestion

        def to_dict(self) -> dict:
            return {
                "refactoring_type": self.suggestion.refactoring_type,
                "file_path": self.suggestion.file_path,
                "target_symbol": self.suggestion.target_symbol,
                "content": "",
                "diff": "",
                "provider": "stub",
                "model": "stub",
                "cached": False,
                "input_tokens": 0,
                "output_tokens": 0,
                "validation": self.suggestion.validation,
            }

    async def enrich(suggestion, **_kwargs):
        seen.append(suggestion.validation)
        return _Generated(suggestion)

    monkeypatch.setattr(llm, "llm_enrichment_enabled", lambda _config: True)
    monkeypatch.setattr(
        "repowise.server.provider_config.get_chat_provider_instance", lambda **_k: object()
    )
    monkeypatch.setattr(llm, "enrich_suggestion", enrich)
    response = await client.post(f"/api/repos/{repo_id}/refactoring/{listed[0]['id']}/generate-code")
    assert response.status_code == 200, response.text
    assert seen == [listed[0]["validation"]]


async def test_resolving_a_ranked_plan_drops_it_and_keeps_the_rest_in_order(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _seed_ranked(client, app)
    before = (await client.get(f"/api/repos/{repo_id}/refactoring/targets")).json()["plans"]
    gone = before[1]["id"]
    patched = await client.patch(
        f"/api/repos/{repo_id}/refactoring/{gone}/status", json={"status": "resolved"}
    )
    assert patched.status_code == 200
    _no_live_ranking(monkeypatch)
    after = (await client.get(f"/api/repos/{repo_id}/refactoring/targets")).json()["plans"]
    assert [item["id"] for item in after] == [item["id"] for item in before if item["id"] != gone]


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


async def test_an_accepted_decision_governs_the_plans_on_its_file(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.persistence.crud import bulk_upsert_decisions
    from repowise.core.persistence.crud.authority import accept_decision
    from repowise.core.persistence.models import DecisionRecord, RefactoringOpportunity

    repo_id = await _seed_ranked(client, app)
    async with app.state.session_factory() as session:
        await bulk_upsert_decisions(
            session,
            repo_id,
            [
                {
                    "title": "Keep the parser in one function",
                    "decision": "Keep the parser in one function",
                    "rationale": "profiling showed the call overhead",
                    "source": "session",
                    "status": "proposed",
                    "affected_files": ["pkg/leaf.py"],
                    "evidence_file": "pkg/leaf.py",
                    "confidence": 0.9,
                    "verification": "exact",
                    "source_quote": "Keep the parser in one function",
                }
            ],
        )
        record = (
            await session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
            )
        ).scalar_one()
        await accept_decision(session, record, accepter="tester")
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
        steps = [
            step
            for row in (
                await session.execute(
                    select(RefactoringOpportunity).where(
                        RefactoringOpportunity.repository_id == repo_id
                    )
                )
            ).scalars()
            for step in json.loads(row.details_json or "{}").get("steps", [])
        ]

    _no_live_ranking(monkeypatch)
    listed = (await client.get(f"/api/repos/{repo_id}/refactoring/targets")).json()["plans"]
    for plan in listed:
        assert "governed_by" not in plan and "risks" not in plan
        detail = (await client.get(f"/api/repos/{repo_id}/refactoring/{plan['id']}")).json()
        governed = plan["file_path"] == "pkg/leaf.py"
        assert detail["governed_by"] == ([record.id] if governed else [])
        decisions = [r for r in detail["risks"] if r["kind"] == "decision"]
        assert [r["ref"] for r in decisions] == ([record.id] if governed else [])
    leaf = [step for step in steps if step["file_path"] == "pkg/leaf.py"]
    assert leaf and all(
        step["applicability"]["classification"] == "judgment"
        and "governed_by_decision" in step["applicability"]["reasons"]
        for step in leaf
    )

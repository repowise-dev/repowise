"""Finalize annotates plans before composing them, without changing the composition.

Runs with the finding registry as shipped, so the seeded ``dry_violation``
clone plan is withheld: it is neither ranked, annotated, nor composed.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from repowise.core.analysis.health.refactoring import opportunity
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.refactoring import _refactoring_row_kwargs
from repowise.core.persistence.crud.authority import accept_decision
from repowise.core.persistence.models import DecisionRecord, RefactoringSuggestion

from .test_refactoring import _seed
from .test_refactoring_plan_rank import _EXTRA


async def _seed_plans(client: AsyncClient, app) -> str:
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        for plan in _EXTRA:
            session.add(RefactoringSuggestion(**_refactoring_row_kwargs(plan, repo_id)))
        await session.commit()
    return repo_id


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict, list]]:
    calls: list[tuple[dict, list]] = []
    compose = opportunity.compose_opportunities

    def recording(rows, **kwargs):
        result = compose(rows, **kwargs)
        calls.append((kwargs, result))
        return result

    monkeypatch.setattr(opportunity, "compose_opportunities", recording)
    return calls


async def _plan_rows(app, repo_id: str) -> list[RefactoringSuggestion]:
    async with app.state.session_factory() as session:
        return list(
            (
                await session.execute(
                    select(RefactoringSuggestion).where(
                        RefactoringSuggestion.repository_id == repo_id,
                        RefactoringSuggestion.refactoring_type != "performance_fix",
                    )
                )
            ).scalars()
        )


async def test_ranking_first_composes_the_same_opportunities_in_the_same_order(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _seed_plans(client, app)
    before = opportunity.compose_opportunities(await _plan_rows(app, repo_id), governed=set())
    calls = _capture(monkeypatch)
    async with app.state.session_factory() as session:
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()

    ((kwargs, composed),) = calls
    assert kwargs["governed"] == set()
    # No findings are seeded, so the rows alone decide ids and order.
    assert [item.opportunity_id for item in composed] == [item.opportunity_id for item in before]
    assert [item.steps for item in composed] == [item.steps for item in before]


async def test_a_withheld_plan_is_neither_annotated_nor_composed_nor_governed(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_id = await _seed_plans(client, app)
    async with app.state.session_factory() as session:
        await crud.bulk_upsert_decisions(
            session,
            repo_id,
            [
                {
                    "title": "The hub stays one module",
                    "decision": "The hub stays one module",
                    "rationale": "import order is load-bearing",
                    "source": "session",
                    "status": "proposed",
                    "affected_files": ["pkg/hub.py"],
                    "evidence_file": "pkg/hub.py",
                    "confidence": 0.9,
                    "verification": "exact",
                    "source_quote": "The hub stays one module",
                }
            ],
        )
        record = (await session.execute(select(DecisionRecord))).scalar_one()
        await accept_decision(session, record, accepter="tester")
        await session.commit()
    calls = _capture(monkeypatch)
    async with app.state.session_factory() as session:
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()

    rows = {row.public_id: row for row in await _plan_rows(app, repo_id)}
    withheld = [row for row in rows.values() if row.source_biomarker == "dry_violation"]
    assert withheld and all(row.rank_json is None for row in withheld)
    ((kwargs, composed),) = calls
    governed = kwargs["governed"]
    # The shown structural plan on the governed file is governed; the withheld
    # clone plan on the same file is not, and is in no opportunity.
    assert governed == {
        public_id
        for public_id, row in rows.items()
        if row.file_path == "pkg/hub.py" and row.rank_json is not None
    }
    assert governed and not governed & {row.public_id for row in withheld}
    composed_ids = {
        item.plan_id for opp in composed for item in (*opp.steps, *opp.evidence)
    }
    assert not composed_ids & {row.public_id for row in withheld}

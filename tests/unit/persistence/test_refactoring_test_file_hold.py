"""Refactoring plans and opportunities on test files are held back on every
plan surface, as Fix first already leaves them out, and each read that counts
what it held back says so under ``test``.

Mirrors the JVM audit: ``scope=all`` and the plan lists served extract_method
plans on ``src/test`` and ``internalClusterTest`` files.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.finding_registry import FILE_HOLDS
from repowise.core.analysis.health.refactoring import RefactoringSuggestion
from repowise.core.persistence.crud.analysis import (
    get_refactoring_suggestions,
    save_refactoring_suggestions,
)
from tests.unit.persistence.helpers import insert_repo

_TEST = "src/test/java/org/acme/HeaderValidatorTests.java"
_PROD = "src/main/java/org/acme/HeaderValidator.kt"


def _plan(path: str) -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path=path,
        target_symbol="validate",
        line_start=10,
        line_end=60,
        plan={},
        evidence={},
        impact_delta=1.0,
        effort_bucket="S",
        blast_radius={},
        confidence="high",
        source_biomarker="complex_method",
    )


async def _seed(session) -> str:
    from repowise.core.analysis.health.refactoring.identity import REFACTORING_MODEL_VERSION
    from repowise.core.persistence.crud import upsert_health_metrics
    from repowise.core.persistence.models import RefactoringOpportunity

    repo = await insert_repo(session)
    await upsert_health_metrics(
        session,
        repo.id,
        [
            {"file_path": _TEST, "score": 5.0, "nloc": 200, "is_test": True},
            {"file_path": _PROD, "score": 5.0, "nloc": 200, "is_test": False},
        ],
    )
    await save_refactoring_suggestions(session, repo.id, [_plan(_TEST), _plan(_PROD)])
    session.add_all(
        RefactoringOpportunity(
            repository_id=repo.id,
            opportunity_id=f"refop_{i}",
            refactoring_model_version=REFACTORING_MODEL_VERSION,
            rank_position=i,
            queue_position=i,
            rank_score=1.0,
            lead_biomarker="complex_method",
            judgment_steps=0,
            evidence_total=0,
            affected_files_total=1,
            details_json="{}",
            file_path=path,
            recoverable_health=1.0,
            step_count=1,
            lead_refactoring_type="extract_method",
            confidence="high",
            effort_bucket="S",
            mechanical_steps=1,
            status="open",
            queue_eligible=path == _PROD,
            queue_reason=None if path == _PROD else "test",
        )
        for i, path in enumerate((_TEST, _PROD))
    )
    await session.commit()
    return repo.id


@pytest.mark.asyncio
async def test_plan_lists_hold_back_test_files(async_session):
    from repowise.core.persistence.crud.analysis.refactoring import gated_plan_counts

    rid = await _seed(async_session)
    assert [p.file_path for p in await get_refactoring_suggestions(async_session, rid)] == [_PROD]
    assert await gated_plan_counts(async_session, rid) == {
        "test": {"count": 1, "reason": FILE_HOLDS["test"]}
    }
    opted = await get_refactoring_suggestions(async_session, rid, include_unverified=True)
    assert {p.file_path for p in opted} == {_TEST, _PROD}


@pytest.mark.asyncio
async def test_opportunity_lists_and_counts_hold_back_test_files(async_session):
    from repowise.core.persistence.crud.analysis.queue_counts import unit_counts
    from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
        gated_opportunity_counts,
        list_refactoring_opportunities,
    )

    rid = await _seed(async_session)
    # ``scope=all`` reads the whole open inventory, eligible or not.
    rows, total = await list_refactoring_opportunities(async_session, rid)
    assert [r.file_path for r in rows] == [_PROD] and total == 1
    assert (await gated_opportunity_counts(async_session, rid))["test"]["count"] == 1
    counts = (await unit_counts(async_session, rid, "plans")).as_dict()
    assert counts["gated"] == 1
    rows, total = await list_refactoring_opportunities(async_session, rid, include_unverified=True)
    assert total == 2

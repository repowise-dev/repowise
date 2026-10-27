"""Refactoring suggestions survive the persistence round trip.

Locks both writers — the full-reindex ``save_refactoring_suggestions`` and
the incremental ``upsert_refactoring_suggestions`` — plus the JSON payload
columns, so a future edit that forgets a field is caught.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.refactoring import RefactoringSuggestion
from repowise.core.persistence.crud.analysis import (
    get_refactoring_suggestions,
    save_refactoring_suggestions,
    upsert_refactoring_suggestions,
)
from tests.unit.persistence.helpers import insert_repo


def _suggestion(path: str, target: str, **overrides) -> RefactoringSuggestion:
    base = dict(
        refactoring_type="extract_class",
        file_path=path,
        target_symbol=target,
        line_start=1,
        line_end=80,
        plan={"groups": [{"name": None, "methods": ["m1", "m2"], "fields": ["a"]}]},
        evidence={"lcom4": 2, "method_count": 6, "field_count": 2, "wmc": 9},
        impact_delta=2.4,
        effort_bucket="M",
        blast_radius={"dependents_count": 3},
        confidence="high",
        source_biomarker="low_cohesion",
    )
    base.update(overrides)
    return RefactoringSuggestion(**base)


@pytest.mark.asyncio
async def test_save_refactoring_suggestions_round_trip(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()

    rows = await get_refactoring_suggestions(async_session, repo.id)
    assert len(rows) == 1
    r = rows[0]
    assert r.refactoring_type == "extract_class"
    assert r.target_symbol == "Foo"
    assert r.impact_delta == 2.4
    assert r.effort_bucket == "M"
    assert r.confidence == "high"
    assert r.source_biomarker == "low_cohesion"
    import json

    assert json.loads(r.plan_json)["groups"][0]["fields"] == ["a"]
    assert json.loads(r.evidence_json)["wmc"] == 9
    assert json.loads(r.blast_radius_json)["dependents_count"] == 3


@pytest.mark.asyncio
async def test_save_is_idempotent(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    rows = await get_refactoring_suggestions(async_session, repo.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_get_filters_by_type_and_confidence(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(
        async_session,
        repo.id,
        [
            _suggestion("a.py", "Foo", confidence="high", impact_delta=3.0),
            _suggestion("b.py", "Bar", confidence="medium", impact_delta=1.0),
        ],
    )
    await async_session.commit()

    # Highest impact first.
    rows = await get_refactoring_suggestions(async_session, repo.id)
    assert [r.target_symbol for r in rows] == ["Foo", "Bar"]

    high_only = await get_refactoring_suggestions(async_session, repo.id, min_confidence="high")
    assert [r.target_symbol for r in high_only] == ["Foo"]

    typed = await get_refactoring_suggestions(
        async_session, repo.id, refactoring_type="extract_class"
    )
    assert len(typed) == 2


@pytest.mark.asyncio
async def test_get_breaks_impact_ties_deterministically(async_session):
    repo = await insert_repo(async_session)
    # Same impact (the common 0.0 no-finding case) across several files — the
    # read order must be stable, keyed (file_path, target_symbol), not DB order.
    await save_refactoring_suggestions(
        async_session,
        repo.id,
        [
            _suggestion("z.py", "Zeta", impact_delta=0.0),
            _suggestion("a.py", "Alpha", impact_delta=0.0),
            _suggestion("m.py", "Mu", impact_delta=0.0),
        ],
    )
    await async_session.commit()
    rows = await get_refactoring_suggestions(async_session, repo.id)
    assert [r.file_path for r in rows] == ["a.py", "m.py", "z.py"]


@pytest.mark.asyncio
async def test_upsert_only_touches_given_files(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(
        async_session,
        repo.id,
        [_suggestion("a.py", "Foo"), _suggestion("b.py", "Bar")],
    )
    await async_session.commit()

    # Re-run for a.py only: a.py's rows are replaced, b.py's are untouched, and
    # a now-clean a.py (empty list scoped to its path) is cleared.
    await upsert_refactoring_suggestions(async_session, repo.id, [], file_paths=["a.py"])
    await async_session.commit()
    rows = await get_refactoring_suggestions(async_session, repo.id)
    assert [r.target_symbol for r in rows] == ["Bar"]


@pytest.mark.asyncio
async def test_type_scoped_upsert_preserves_nonperformance_plans(async_session):
    repo = await insert_repo(async_session)
    performance = _suggestion(
        "a.py",
        "a.py::fetch",
        refactoring_type="performance_fix",
        source_biomarker="io_in_loop",
        plan={"strategy": "batch_or_prefetch_io"},
    )
    await save_refactoring_suggestions(
        async_session,
        repo.id,
        [_suggestion("a.py", "Foo"), performance],
    )
    await upsert_refactoring_suggestions(
        async_session,
        repo.id,
        [],
        file_paths=["a.py"],
        refactoring_type="performance_fix",
    )
    await async_session.commit()
    rows = await get_refactoring_suggestions(async_session, repo.id)
    assert [(row.refactoring_type, row.target_symbol) for row in rows] == [("extract_class", "Foo")]


@pytest.mark.asyncio
async def test_performance_only_partial_scope_clears_stale_plan(async_session):
    from repowise.core.pipeline.incremental import persist_partial_health

    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(
        async_session,
        repo.id,
        [
            _suggestion("caller.py", "Foo"),
            _suggestion(
                "caller.py",
                "caller.py::run",
                refactoring_type="performance_fix",
                source_biomarker="io_in_loop",
            ),
        ],
    )
    report = SimpleNamespace(
        authoritative_paths=set(),
        performance_authoritative_paths={"caller.py"},
        metrics=[],
        findings=[],
        refactoring_suggestions=[],
        function_blame_rows=[],
    )

    await persist_partial_health(async_session, repo.id, report)
    await async_session.commit()

    rows = await get_refactoring_suggestions(async_session, repo.id)
    assert [(row.refactoring_type, row.target_symbol) for row in rows] == [("extract_class", "Foo")]


@pytest.mark.asyncio
async def test_a_partial_run_never_persists_its_own_performance_plans(async_session):
    """A partial run sees a subset of the findings, so its plans are a subset.

    The plans it carries are a report-level convenience. The authoritative ones
    are rebuilt from the merged stored findings by the writer both index paths
    share, which is also what retired the path bookkeeping this writer used to
    need to reach a shared intervention downstream of its callers.
    """
    from repowise.core.pipeline.incremental import persist_partial_health

    repo = await insert_repo(async_session)
    performance = _suggestion(
        "shared.py",
        "shared.py::load",
        refactoring_type="performance_fix",
        source_biomarker="io_in_loop",
        plan={
            "opportunity_id": "opp-shared",
            "strategy": "batch_or_prefetch_io",
            "affected_locations": [{"file_path": "caller.py", "line_start": 10}],
        },
    )
    report = SimpleNamespace(
        authoritative_paths=set(),
        performance_authoritative_paths={"caller.py"},
        metrics=[],
        findings=[],
        refactoring_suggestions=[performance],
        function_blame_rows=[],
    )

    await persist_partial_health(async_session, repo.id, report)
    await async_session.commit()

    # No stored findings, so the rebuild leaves no performance plan behind.
    assert await get_refactoring_suggestions(async_session, repo.id) == []

"""The Fix-first loader agrees with the pure builder over the same rows."""

from __future__ import annotations

import json

from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.persistence.crud.analysis.fix_first import load_fix_first
from repowise.core.persistence.models import (
    GitMetadata,
    GraphMetric,
    HealthFileMetric,
    HealthFinding,
    PerformanceOpportunity,
    RefactoringOpportunity,
    RefactoringSuggestion,
)
from tests.unit.health.fix_first_rows import FINDINGS, METRICS, PERFORMANCE, PLANS, REFACTORING
from tests.unit.persistence.helpers import insert_repo


def _with_json(row: dict, name: str) -> dict:
    out = {k: v for k, v in row.items() if k != name}
    out[f"{name}_json"] = json.dumps(row.get(name) or {})
    return out


async def seed_fix_first(session, rid: str | None = None) -> str:
    rid = rid or (await insert_repo(session)).id
    for m in METRICS:
        session.add(
            HealthFileMetric(
                repository_id=rid,
                **{k: m[k] for k in ("file_path", "score", "nloc", "is_test",
                                     "analyzed_commit", "updated_at")},
                code_origin=m.get("code_origin"),
            )
        )
        session.add(GitMetadata(repository_id=rid, file_path=m["file_path"],
                                commit_count_90d=m["commit_count_90d"]))
        session.add(GraphMetric(repository_id=rid, node_id=m["file_path"],
                                in_degree=m["dependents"]))
    session.add_all(HealthFinding(repository_id=rid, **_with_json(f, "details")) for f in FINDINGS)
    session.add_all(
        RefactoringOpportunity(repository_id=rid, **_with_json(r, "details")) for r in REFACTORING
    )
    session.add_all(
        PerformanceOpportunity(repository_id=rid, **_with_json(p, "details")) for p in PERFORMANCE
    )
    for p in PLANS:
        session.add(
            RefactoringSuggestion(
                repository_id=rid,
                file_path="src/core.py",
                public_id=p["public_id"],
                refactoring_type=p["refactoring_type"],
                evidence_json=json.dumps(p["evidence"]),
                plan_json=json.dumps(p["plan"]),
            )
        )
    await session.flush()
    return rid


async def test_loader_matches_the_builder_over_rows(async_session) -> None:
    rid = await seed_fix_first(async_session)
    loaded = await load_fix_first(async_session, rid)
    built = build_fix_first(
        metrics=METRICS,
        findings=FINDINGS,
        refactoring=REFACTORING,
        performance=PERFORMANCE,
        plans=PLANS,
    )
    assert loaded == built
    # The seed exercises every kind, so equality is not over an empty queue.
    assert {i.kind for i in loaded.items} == {"refactor", "perf_fix", "finding"}
    assert loaded.basis["analyzed_commit"] == "abc"


async def test_loader_on_an_empty_repository(async_session) -> None:
    rid = (await insert_repo(async_session)).id
    queue = await load_fix_first(async_session, rid)
    assert queue.items == () and queue.totals.candidates == 0


async def test_the_queue_is_cached_until_a_store_changes(async_session) -> None:
    from sqlalchemy import select

    rid = await seed_fix_first(async_session)
    first = await load_fix_first(async_session, rid)
    assert await load_fix_first(async_session, rid) is first
    # Triage moves a finding's updated_at, so the next read rebuilds.
    row = (
        await async_session.execute(
            select(HealthFinding).where(HealthFinding.public_id == "finding_n1")
        )
    ).scalar_one()
    row.status = "acknowledged"
    await async_session.flush()
    rebuilt = await load_fix_first(async_session, rid)
    assert rebuilt is not first
    assert all(i.target.file_path != "src/plain.py" for i in rebuilt.items)


async def test_loader_reads_the_stored_code_origin(async_session) -> None:
    from sqlalchemy import update

    rid = await seed_fix_first(async_session)
    await async_session.execute(
        update(HealthFileMetric)
        .where(HealthFileMetric.file_path == "src/plain.py")
        .values(code_origin="docs_example")
    )
    await async_session.flush()
    loaded = await load_fix_first(async_session, rid)
    metrics = [
        {**m, "code_origin": "docs_example"} if m["file_path"] == "src/plain.py" else m
        for m in METRICS
    ]
    built = build_fix_first(
        metrics=metrics,
        findings=FINDINGS,
        refactoring=REFACTORING,
        performance=PERFORMANCE,
        plans=PLANS,
    )
    assert loaded.totals.excluded["docs_example"] == 1
    # The update moved the metric's ``updated_at``, so only the basis differs.
    assert (loaded.items, loaded.totals) == (built.items, built.totals)

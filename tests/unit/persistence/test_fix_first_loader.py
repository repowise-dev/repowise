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


async def test_loader_reads_verified_duplicates_as_a_fact(async_session) -> None:
    """A dispatch-heavy function stays a candidate when a verified duplicate
    sits in it: an Extract Helper plan stored at another file names it."""
    from sqlalchemy import select

    rid = await seed_fix_first(async_session)
    row = (
        await async_session.execute(
            select(HealthFinding).where(HealthFinding.public_id == "finding_c1")
        )
    ).scalar_one()
    dispatch = {**json.loads(row.details_json), "dispatch_share": 0.9}
    row.details_json = json.dumps(dispatch)
    helper = {
        "public_id": "refac3_helper", "refactoring_type": "extract_helper",
        "file_path": "src/other.py", "target_symbol": "other",
        "evidence": {"duplicated_lines": 15},
        "plan": {"occurrences": [
            {"file": "src/other.py", "line_start": 5, "line_end": 20},
            {"file": "src/core.py", "line_start": 30, "line_end": 44},
        ]},
    }
    async_session.add(
        RefactoringSuggestion(
            repository_id=rid,
            public_id=helper["public_id"],
            refactoring_type="extract_helper",
            file_path="src/other.py",
            target_symbol="other",
            status="open",
            evidence_json=json.dumps(helper["evidence"]),
            plan_json=json.dumps(helper["plan"]),
        )
    )
    await async_session.flush()
    findings = [
        {**f, "details": dispatch} if f["public_id"] == "finding_c1" else f for f in FINDINGS
    ]
    built = build_fix_first(
        metrics=METRICS,
        findings=findings,
        refactoring=REFACTORING,
        performance=PERFORMANCE,
        plans=[*PLANS, helper],
    )
    loaded = await load_fix_first(async_session, rid)
    assert "src/core.py" in {i.target.file_path for i in loaded.items}
    assert (loaded.items, loaded.totals) == (built.items, built.totals)


async def test_loader_reads_extractions_for_a_finding_without_a_plan(async_session) -> None:
    """A size finding takes its first step from an Extract Method plan stored
    for its function, though no opportunity carries that plan."""
    rid = await seed_fix_first(async_session)
    extraction = {
        "public_id": "refac2_walk", "refactoring_type": "extract_method",
        "file_path": "src/plain.py", "target_symbol": "walk",
        "evidence": {"slice_nloc": 8, "ccn_removed": 2},
        "plan": {"span": {"start": 12, "end": 20}, "suggested_name": "step"},
    }
    async_session.add(
        RefactoringSuggestion(
            repository_id=rid,
            public_id=extraction["public_id"],
            refactoring_type="extract_method",
            file_path="src/plain.py",
            target_symbol="walk",
            status="open",
            evidence_json=json.dumps(extraction["evidence"]),
            plan_json=json.dumps(extraction["plan"]),
        )
    )
    await async_session.flush()
    loaded = await load_fix_first(async_session, rid)
    walk = next(i for i in loaded.items if i.target.file_path == "src/plain.py")
    assert walk.action.steps[0].text == "Extract lines 12-20 of walk into step()"
    built = build_fix_first(
        metrics=METRICS,
        findings=FINDINGS,
        refactoring=REFACTORING,
        performance=PERFORMANCE,
        plans=[*PLANS, extraction],
    )
    assert (loaded.items, loaded.totals) == (built.items, built.totals)

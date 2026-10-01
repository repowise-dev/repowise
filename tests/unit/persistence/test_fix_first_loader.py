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


async def _seed(session) -> str:
    rid = (await insert_repo(session)).id
    for m in METRICS:
        session.add(
            HealthFileMetric(
                repository_id=rid,
                **{k: m[k] for k in ("file_path", "score", "nloc", "is_test",
                                     "analyzed_commit", "updated_at")},
            )
        )
        session.add(GitMetadata(repository_id=rid, file_path=m["file_path"],
                                commit_count_90d=m["commit_count_90d"]))
        session.add(GraphMetric(repository_id=rid, node_id=m["file_path"],
                                in_degree=m["dependents"]))
    session.add_all(HealthFinding(repository_id=rid, **f) for f in FINDINGS)
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
    rid = await _seed(async_session)
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

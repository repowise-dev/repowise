"""The Fix-first loader agrees with the pure builder over the same rows."""

from __future__ import annotations

import json

from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.refactoring.identity import REFACTORING_MODEL_VERSION
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
        RefactoringOpportunity(
            repository_id=rid,
            refactoring_model_version=REFACTORING_MODEL_VERSION,
            **_with_json(r, "details"),
        )
        for r in REFACTORING
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


async def test_every_limit_and_id_is_a_slice_of_one_build(async_session, monkeypatch) -> None:
    from repowise.core.persistence.crud.analysis import fix_first as loader

    rid = await seed_fix_first(async_session)
    builds = 0
    original = loader._build

    async def counting(*args, **kwargs):
        nonlocal builds
        builds += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(loader, "_build", counting)
    loader.clear_fix_first_cache()
    rows = {"metrics": METRICS, "findings": FINDINGS, "refactoring": REFACTORING,
            "performance": PERFORMANCE, "plans": PLANS}

    full = await load_fix_first(async_session, rid, limit=None)
    assert full == build_fix_first(**rows, limit=None)
    assert await load_fix_first(async_session, rid, limit=1) == build_fix_first(**rows, limit=1)
    assert await load_fix_first(async_session, rid) == build_fix_first(**rows)
    second = full.items[1].id
    assert await load_fix_first(async_session, rid, item_id=second) == build_fix_first(
        **rows, item_id=second
    )
    assert builds == 1


async def test_a_finding_item_verifies_with_measured_tests_of_its_lines(async_session) -> None:
    """A finding with no plan takes its tests from coverage of its own lines."""
    from repowise.core.persistence.models import TestCoverageEntry

    rid = await seed_fix_first(async_session)
    for test_id, lines in (
        ("tests/test_plain.py::test_walk", [6, 7, 8]),
        ("tests/test_plain.py::test_elsewhere", [50, 51]),
    ):
        async_session.add(
            TestCoverageEntry(
                repository_id=rid,
                test_id=test_id,
                test_file="tests/test_plain.py",
                source_file="src/plain.py",
                covered_lines_json=json.dumps(lines),
                source_format="coverage_json",
            )
        )
    await async_session.flush()
    loaded = await load_fix_first(async_session, rid)
    walk = next(i for i in loaded.items if i.target.file_path == "src/plain.py")
    assert walk.kind == "finding"
    assert walk.verify.basis == "measured"
    assert [t.path for t in walk.verify.tests] == ["tests/test_plain.py::test_walk"]
    assert walk.verify.tests_total == 1
    assert walk.verify.command == "pytest tests/test_plain.py::test_walk"


async def test_a_finding_item_with_no_tests_stays_unknown(async_session) -> None:
    rid = await seed_fix_first(async_session)
    loaded = await load_fix_first(async_session, rid)
    walk = next(i for i in loaded.items if i.target.file_path == "src/plain.py")
    assert (walk.verify.tests, walk.verify.basis) == ((), "unknown")


async def test_loader_counts_dormant_causes_and_dead_code(async_session) -> None:
    from repowise.core.persistence.models import DeadCodeFinding
    from tests.unit.health.fix_first_rows import _perf

    rid = (await insert_repo(async_session)).id
    async_session.add(HealthFileMetric(repository_id=rid, file_path="src/repo.py", score=8.0,
                                       nloc=90, is_test=False))
    gated = _perf("perf3_gated", "x", actionability_state="expected", plan_state="no_safe_plan",
                  fix_strategy=None, intervention_symbol="src/repo.py::off")
    gated = _with_json(gated, "details")
    gated["details_json"] = json.dumps({"actionability_reason": "gated_off"})
    live = _perf("perf3_live", "y")
    async_session.add_all([
        PerformanceOpportunity(repository_id=rid, **gated),
        PerformanceOpportunity(repository_id=rid, **_with_json(live, "details")),
        DeadCodeFinding(repository_id=rid, kind="unused_internal", file_path="src/repo.py",
                        symbol_name="load_all", confidence=0.9, status="open"),
    ])
    await async_session.flush()
    queue = await load_fix_first(async_session, rid)
    assert queue.items == ()
    assert queue.totals.excluded["gated_off"] == 1 and queue.totals.dormant == 1
    assert queue.totals.excluded["unreachable"] == 1


def test_json_text_renders_for_both_dialects() -> None:
    from sqlalchemy import column, select
    from sqlalchemy.dialects import postgresql, sqlite

    from repowise.core.persistence.sql import json_text

    query = select(json_text(column("details_json"), "actionability_reason"))
    assert "json_extract(details_json" in str(query.compile(dialect=sqlite.dialect()))
    assert "CAST(details_json AS JSON) ->>" in str(query.compile(dialect=postgresql.dialect()))


async def test_an_unverified_read_skips_the_test_lookup(async_session, monkeypatch) -> None:
    """The actions view never shows tests, so it must not pay for resolving them.

    The verified queue, once built, answers an unverified read too.
    """
    from repowise.core.persistence.crud.analysis import fix_first as loader

    rid = await seed_fix_first(async_session)
    loader.clear_fix_first_cache()
    calls = 0
    original = loader._finding_validator

    async def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(loader, "_finding_validator", counting)
    bare = await load_fix_first(async_session, rid, verify=False)
    assert calls == 0
    assert [i.id for i in bare.items] == [i.id for i in (await load_fix_first(async_session, rid)).items]
    assert calls == 1
    assert (await load_fix_first(async_session, rid, limit=None, verify=False)).items == (
        await load_fix_first(async_session, rid, limit=None)
    ).items
    assert calls == 1

"""The session-free half of performance serving, and its agreement with the store.

The sort, filter and facet rules are one table read two ways: the store builds
SQL from it and ``keep``/``row_sort_key``/``facet_counts`` read it over rows in
memory. The agreement tests run both over the same rows.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.core.analysis.health.perf.opportunities import PERFORMANCE_MODEL_VERSION
from repowise.core.analysis.health.perf.serving import (
    SORTS,
    SUMMARY_UNAVAILABLE,
    PerformanceQuery,
    facet_counts,
    fold_facets,
    fold_scoped_facets,
    intervention_file,
    keep,
    parse_query,
    plan_link,
    rescope_summary,
    row_sort_key,
    serialize,
    summary_payload,
)
from repowise.core.analysis.health.rows import field

# ---------------------------------------------------------------------------
# Pure shapes
# ---------------------------------------------------------------------------


def test_parse_query_reports_what_it_discards() -> None:
    query, ignored = parse_query(
        context="nowhere",
        boundary="tape",
        confidence="high",
        actionability="maybe",
        view="wide",
        sort="random",
        limit=-3,
        offset=-1,
    )
    assert query == PerformanceQuery(confidence="high", limit=0, offset=0)
    assert set(ignored) == {
        "performance_context",
        "performance_boundary",
        "performance_actionability",
        "performance_view",
        "performance_sort",
    }
    assert ignored["performance_sort"] == "random (accepted: rank, leverage, observations)"


def test_query_resolves_contexts_and_default_queue() -> None:
    assert parse_query(context="all")[0].contexts is None
    assert parse_query(context="production_tooling")[0].contexts == {"production", "tooling"}
    # The default queue is the stored judgement; any other context keeps its states.
    assert parse_query()[0].queue_eligible is True and parse_query()[0].actionabilities is None
    assert parse_query(context="all")[0].actionabilities == {"plan_ready", "advisory"}
    assert parse_query(context="all")[0].queue_eligible is None
    assert parse_query(actionability="expected")[0].actionabilities == {"expected"}
    assert parse_query(boundary="none")[0].boundary == "none"


def test_plan_link_states() -> None:
    assert plan_link({"plan_state": "no_safe_plan"}, None).state == "no_safe_plan"
    # A stored "available" with no plan row is a plan this index lacks.
    missing = plan_link({"plan_state": "available"}, None)
    assert missing.state == "not_persisted" and "Reindex" in missing.reason
    linked = plan_link({"plan_state": "available"}, {"id": 7}, "plan:x")
    assert (linked.state, linked.row_id, linked.public_id) == ("available", "7", "plan:x")


def test_summary_view_keeps_what_a_row_is_acted_on_by() -> None:
    row = {
        "opportunity_id": "perf1",
        "biomarker_type": "io_in_loop",
        "boundary_kind": "db",
        "fix_strategy": "batch_or_prefetch_io",
        "fix_safety": "advisory",
        "details": {
            "facets": {"amplification": "per_iteration", "loop_magnitude": "grows_with_data"},
            "fix_rationale": "One sink.",
            "plan": {"effort_bucket": "S", "benefit": 1.0, "cost": 1.0, "risk": 1.0,
                     "steps": [{"order": 1}], "validation": {"total": 0}},
        },
    }
    link = plan_link({"plan_state": "no_safe_plan"}, None)
    # The agent surface's summary shape does not grow.
    bare = serialize(row, link, summary=True)
    assert not {"fix", "gain_text", "plan_economics"} & bare.keys()
    summary = serialize(row, link, summary=True, decisions=True)
    assert summary["fix"] == {
        "strategy": "batch_or_prefetch_io", "safety": "advisory", "rationale": "One sink."
    }
    assert summary["gain_text"]
    assert summary["plan_economics"]["effort_bucket"] == "S"
    # The heavy plan fields stay on the detail view only.
    assert "plan_steps" not in summary and "validation" not in summary
    detail = serialize(row, plan_link({"plan_state": "no_safe_plan"}, None))
    assert detail["plan_steps"] == [{"order": 1}]
    assert detail["fix"] == summary["fix"]


def test_intervention_file_prefers_the_symbol() -> None:
    def opp(symbol: str | None, evidence: list[dict[str, Any]]) -> Any:
        return SimpleNamespace(intervention_symbol=symbol, evidence=evidence)

    assert intervention_file(opp("a/b.py::f", [{"file_path": "c.py"}])) == "a/b.py"
    assert intervention_file(opp(None, [{"file_path": "c.py"}])) == "c.py"
    assert intervention_file(opp(None, [])) == ""


def test_summary_payload_names_absence_and_staleness() -> None:
    assert summary_payload(None) == SUMMARY_UNAVAILABLE
    row = {
        "summary_json": '{"actionability": {"advisory": 2}, "default_queue": {"x": 1}}',
        "performance_model_version": PERFORMANCE_MODEL_VERSION - 1,
        "analyzed_commit": "c",
        "opportunities_total": 2,
    }
    payload = summary_payload(row)
    assert payload["status"] == "stale_model" and payload["refresh_required"] is True
    assert payload["actionability"] == {"advisory": 2} and payload["default_queue"] == {"x": 1}
    assert summary_payload({**row, "summary_json": "[1]"})["actionability"] == {}


def test_rescope_summary_recounts_one_context() -> None:
    groups = [
        ("production", None, "high", "advisory", "available", "proven", "request", 2),
        ("production", "db", "low", "plan_ready", "no_safe_plan", "unproven", "cli", 1),
        ("test", "db", "low", "advisory", "available", "proven", "test", 5),
    ]
    scoped = rescope_summary({"total": 8, "x": 1}, groups, frozenset({"production"}))
    assert scoped == {
        "total": 3,
        "x": 1,
        "actionability": {"advisory": 2, "plan_ready": 1},
        "context": {"production": 3},
        "boundary": {"none": 2, "db": 1},
        "proof": {"proven": 2, "unproven": 1},
        "with_plan_total": 2,
    }


# ---------------------------------------------------------------------------
# One rule table, two readers
# ---------------------------------------------------------------------------

_SEEDS: list[dict[str, Any]] = [
    {"execution_context": "production", "boundary_kind": "db", "evidence_confidence": "high",
     "actionability_state": "plan_ready", "plan_state": "available", "file_path": "a.py",
     "affected_call_sites_total": 4, "observations_total": 9, "cost_proof": "proven", "execution_role": "request"},
    {"execution_context": "production", "boundary_kind": None, "evidence_confidence": "medium",
     "actionability_state": "advisory", "plan_state": "no_safe_plan", "file_path": "b.py",
     "affected_call_sites_total": 4, "observations_total": 2, "cost_proof": "unproven", "execution_role": "cli"},
    {"execution_context": "test", "boundary_kind": "network", "evidence_confidence": "low",
     "actionability_state": "advisory", "plan_state": "not_persisted", "file_path": "a.py",
     "affected_call_sites_total": 7, "observations_total": 2, "cost_proof": "proven", "execution_role": "test"},
    {"execution_context": "tooling", "boundary_kind": None, "evidence_confidence": "high",
     "actionability_state": "expected", "plan_state": "no_safe_plan", "file_path": "c.py",
     "affected_call_sites_total": 1, "observations_total": 9, "cost_proof": "unproven", "execution_role": "tooling"},
    {"execution_context": "production", "boundary_kind": "db", "evidence_confidence": "high",
     "actionability_state": "investigate", "plan_state": "no_safe_plan", "file_path": "c.py",
     "affected_call_sites_total": 0, "observations_total": 5, "cost_proof": "proven", "execution_role": "scheduled_job"},
    {"execution_context": "unknown", "boundary_kind": "filesystem", "evidence_confidence": "medium",
     "actionability_state": "plan_ready", "plan_state": "available", "file_path": "b.py",
     "affected_call_sites_total": 7, "observations_total": 1, "cost_proof": "proven", "execution_role": "unknown"},
    # Resolved: in no queue and no facet.
    {"execution_context": "production", "boundary_kind": "db", "evidence_confidence": "high",
     "actionability_state": "plan_ready", "plan_state": "available", "file_path": "a.py",
     "affected_call_sites_total": 99, "observations_total": 99, "status": "resolved",
     "cost_proof": "proven", "execution_role": "request"},
]

_QUERIES: list[dict[str, Any]] = [
    {},
    {"context": "all"},
    {"context": "test"},
    {"context": "production_tooling", "actionability": "expected"},
    {"context": "all", "boundary": "none"},
    {"context": "all", "boundary": "db", "confidence": "high"},
    {"context": "all", "actionability": "investigate"},
    {"context": "all", "file_paths": ("a.py", "b.py")},
    {"context": "all", "file_paths": ()},
    {"boundary": "network"},
    {"proof": "unproven"},
    {"context": "all", "proof": "unproven", "actionability": "expected"},
    {"context": "all", "role": "all"},
    {"role": "cli"},
    {"context": "all", "role": "scheduled_job", "actionability": "investigate"},
]


def _seed_rows(repository_id: str) -> list[Any]:
    from repowise.core.persistence.models import PerformanceOpportunity

    return [
        PerformanceOpportunity(
            repository_id=repository_id,
            opportunity_id=f"perf_{i}",
            performance_model_version=PERFORMANCE_MODEL_VERSION,
            status=seed.get("status", "open"),
            rank_position=i,
            details_json="{}",
            # The default queue's rule, as the index judges and stores it.
            queue_eligible=(
                seed["execution_context"] == "production"
                and seed["actionability_state"] in ("plan_ready", "advisory")
                and seed["cost_proof"] == "proven"
            ),
            **{k: v for k, v in seed.items() if k != "status"},
        )
        for i, seed in enumerate(_SEEDS)
    ]


def _as_mapping(row: Any) -> dict[str, Any]:
    return {
        column: getattr(row, column)
        for column in (
            "opportunity_id", "status", "rank_position", "queue_eligible", *_SEEDS[0],
        )
    }


@pytest.fixture
async def store(tmp_path: Path):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            session.add_all(_seed_rows(repo.id))
            await session.commit()
            yield session, repo.id
    finally:
        await engine.dispose()


def _both_shapes(repository_id: str) -> list[list[Any]]:
    rows = _seed_rows(repository_id)
    return [rows, [_as_mapping(r) for r in rows]]


@pytest.mark.parametrize("args", _QUERIES)
@pytest.mark.parametrize("sort", [*SORTS])
async def test_keep_and_sort_agree_with_the_store(store, args, sort) -> None:
    from repowise.core.persistence.crud.analysis.performance import (
        list_performance_opportunities,
    )

    session, repository_id = store
    query, _ = parse_query(**args, sort=sort, limit=100)
    stored, total = await list_performance_opportunities(
        session,
        repository_id,
        contexts=query.contexts,
        boundary=query.boundary,
        confidence=query.confidence,
        actionabilities=query.actionabilities,
        proofs=query.proofs,
        roles=query.roles,
        queue_eligible=query.queue_eligible,
        file_paths=query.file_paths,
        sort=query.sort,
        limit=query.limit,
    )
    expected = [row.opportunity_id for row in stored]
    assert total == len(expected)
    for rows in _both_shapes(repository_id):
        kept = sorted((r for r in rows if keep(r, query)), key=lambda r: row_sort_key(r, sort))
        assert [field(r, "opportunity_id") for r in kept] == expected


def _reference_facets(groups: list[tuple], query: PerformanceQuery) -> dict[str, Any]:
    """The cross-filtered fold as the service wrote it before the rules were data."""
    dimensions = {"context": 0, "boundary": 1, "confidence": 2, "actionability": 3,
                  "plan_state": 4, "proof": 5, "role": 6}
    selected = {
        "context": query.contexts,
        "boundary": None if query.boundary is None else frozenset({query.boundary}),
        "confidence": None if query.confidence is None else frozenset({query.confidence}),
        "actionability": (
            None if query.actionability is None else frozenset({query.actionability})
        ),
        "plan_state": None,
        "proof": None if query.proof is None else frozenset({query.proof}),
        "role": None if query.role in (None, "all") else frozenset({query.role}),
    }
    facets = {}
    for name, index in dimensions.items():
        counts: dict[str, int] = {}
        for row in groups:
            if any(
                values is not None and other != name
                and (row[dimensions[other]] or "none") not in values
                for other, values in selected.items()
            ):
                continue
            counts[row[index] or "none"] = counts.get(row[index] or "none", 0) + row[7]
        facets[name] = [
            {"value": v, "total": t}
            for v, t in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
    return facets


@pytest.mark.parametrize("args", _QUERIES)
async def test_facet_counts_agree_with_the_store(store, args) -> None:
    from repowise.core.persistence.crud.analysis.performance import performance_facet_counts

    session, repository_id = store
    query, _ = parse_query(**args)
    grouped = await performance_facet_counts(
        session, repository_id, file_paths=query.file_paths
    )
    expected = fold_facets(grouped, query)
    assert expected == _reference_facets(grouped, query)
    for rows in _both_shapes(repository_id):
        assert facet_counts(rows, query) == expected


async def test_an_unjudged_store_reads_the_default_queue_rule_live(store) -> None:
    from sqlalchemy import update

    from repowise.core.persistence.models import PerformanceOpportunity
    from repowise.server.services.performance_health import PerformanceHealthService

    session, repository_id = store
    service = PerformanceHealthService(session, repository_id, "repo")
    judged = [i["opportunity_id"] for i in (await service.page(parse_query()[0])).items]
    await session.execute(update(PerformanceOpportunity).values(queue_eligible=None))
    await session.commit()
    live = [i["opportunity_id"] for i in (await service.page(parse_query()[0])).items]
    assert live == judged == ["perf_0"]


_SCOPES = [
    # Worth doing now: the stored default-queue verdict.
    {},
    {"queue": True, "actionability": "advisory"},
    # All production causes, every state, proof and role.
    {"actionability": "all", "proof": "all", "role": "all"},
    # Everything.
    {"context": "all", "actionability": "all", "proof": "all", "role": "all"},
    {"context": "all", "actionability": "all", "proof": "all", "role": "all", "boundary": "db"},
    {"context": "test", "actionability": "all", "proof": "all", "role": "all"},
]

#: Facet name and the query attribute that narrows it.
_OWN = {
    "context": "context",
    "boundary": "boundary",
    "confidence": "confidence",
    "actionability": "actionability",
    "proof": "proof",
    "role": "role",
}


@pytest.mark.parametrize("args", _SCOPES)
async def test_scoped_facets_sum_to_the_rows_the_scope_returns(store, args) -> None:
    """Every number the dashboard shows sits inside its scope: a facet with no
    own selection sums to the query's total, and a selected value equals it."""
    from repowise.core.persistence.crud.analysis.performance import (
        list_performance_opportunities,
        performance_facet_counts,
    )

    session, repository_id = store
    query, ignored = parse_query(**args, limit=100, lift_defaults=True)
    assert not ignored
    _, total = await list_performance_opportunities(
        session, repository_id, **query.store_filters(), sort=query.sort, limit=100
    )
    grouped = await performance_facet_counts(session, repository_id, scoped=True)
    facets = fold_scoped_facets(grouped, query)
    assert "queue" not in facets
    for name, attribute in _OWN.items():
        counts = {entry["value"]: entry["total"] for entry in facets[name]}
        chosen = getattr(query, attribute)
        if chosen in (None, "all"):
            assert sum(counts.values()) == total, name
        else:
            assert counts.get(chosen, 0) == total, name


def test_all_lifts_the_default_state_and_proof() -> None:
    query, ignored = parse_query(actionability="all", proof="all", role="all", lift_defaults=True)
    assert not ignored
    assert (query.actionabilities, query.proofs, query.roles) == (None, None, None)
    assert query.queue_eligible is None
    held, _ = parse_query(queue=True, actionability="investigate")
    assert held.queue_eligible is True
    assert held.actionabilities == {"investigate"}
    # The agent surface keeps its vocabulary: "all" is named, not applied.
    _, ignored = parse_query(actionability="all", proof="all")
    assert set(ignored) == {"performance_actionability", "performance_proof"}
    # An explicit proof survives the live default-queue rule on an unjudged store.
    live, _ = parse_query(queue=True, actionability="advisory", proof="proven")
    assert live.store_filters(judged=False)["proofs"] == {"proven"}


async def test_scope_counts_equal_what_each_scope_lists(store) -> None:
    """The scope control's numbers are the totals its scopes return."""
    from repowise.core.analysis.health.perf.serving import SCOPES, scope_counts
    from repowise.core.persistence.crud.analysis.performance import (
        list_performance_opportunities,
        performance_facet_counts,
    )

    session, repository_id = store
    grouped = await performance_facet_counts(session, repository_id, scoped=True)
    counts = scope_counts(grouped)
    assert set(counts) == {"now", "production", "all"}
    for name, args in SCOPES.items():
        query, _ = parse_query(**args, limit=100, lift_defaults=True)
        _, total = await list_performance_opportunities(
            session, repository_id, **query.store_filters(), sort=query.sort, limit=100
        )
        assert counts[name] == total, name
    assert counts["now"] <= counts["production"] <= counts["all"]


def test_area_facet_ranks_areas_and_splits_their_states() -> None:
    from repowise.core.analysis.health.perf.serving import area_facet

    groups = [
        ("pkg/a/x.py", "advisory", 3),
        ("pkg/a/y.py", "expected", 5),
        ("pkg/b/z.py", "plan_ready", 1),
        ("pkg/c/w.py", "investigate", 2),
    ]
    facet = area_facet(groups)
    assert facet["depth"] == 2
    assert [entry["area"] for entry in facet["items"]] == ["pkg/b", "pkg/a", "pkg/c"]
    assert facet["items"][1] == {
        "area": "pkg/a", "total": 8, "actionability": {"advisory": 3, "expected": 5},
    }
    assert facet["total"] == sum(entry["total"] for entry in facet["items"]) + facet["more"]
    # A chosen depth wins; a limit counts the rest in ``more``.
    assert area_facet(groups, depth=1)["items"] == [
        {"area": "pkg", "total": 11, "actionability": {"advisory": 3, "expected": 5,
                                                        "plan_ready": 1, "investigate": 2}},
    ]
    assert area_facet(groups, limit=1)["more"] == 10
    # Files directly in the opened area are its own files, flagged as such.
    inside = area_facet([("pkg/a/x.py", "advisory", 1), ("pkg/a/b/y.py", "advisory", 1)], base="pkg/a")
    own = [entry for entry in inside["items"] if entry.get("own")]
    assert [entry["area"] for entry in own] == ["pkg/a"]


def test_an_area_narrows_by_directory_never_by_a_shared_prefix() -> None:
    query, _ = parse_query(area="pkg/a/", lift_defaults=True)
    assert query.area == "pkg/a"
    assert query.store_filters()["area_prefix"] == "pkg/a/"
    rows = [{"file_path": "pkg/a/x.py", "status": "open"}, {"file_path": "pkg/ab/y.py", "status": "open"}]
    only_area = {"status": "open", "area_prefix": query.area_prefix}
    assert [r["file_path"] for r in rows if keep(r, only_area)] == ["pkg/a/x.py"]
    assert parse_query(area="")[0].store_filters()["area_prefix"] is None

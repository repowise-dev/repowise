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
    intervention_file,
    keep,
    parse_query,
    plan_link,
    rescope_summary,
    row_sort_key,
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

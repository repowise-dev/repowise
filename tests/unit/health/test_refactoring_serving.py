"""The session-free half of refactoring serving, and its agreement with the store.

The sort, filter and facet rules are one table read two ways: the store builds
SQL from it and ``keep``/``row_sort_key``/``facet_counts`` read it over rows in
memory. The agreement tests run both over the same rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repowise.core.analysis.health.refactoring.serving import (
    SORTS,
    UNAVAILABLE,
    directive_from_summary,
    evidence_block,
    facet_counts,
    keep,
    next_actions,
    parse_query,
    plan_payload,
    plan_view,
    row_sort_key,
    serialize,
    stored_validation,
    summary_payload,
    validation_from_profile,
)
from repowise.core.analysis.health.rows import field

# ---------------------------------------------------------------------------
# Pure shapes
# ---------------------------------------------------------------------------


def test_parse_query_reports_what_it_discards() -> None:
    query, ignored = parse_query(
        lead_type="extract_method,bogus",
        status="nope",
        confidence="high",
        effort="XXL",
        view="sideways",
        order="random",
        search="  pkg ",
        limit=-5,
        offset=-1,
    )
    assert query.lead_types == ("extract_method",)
    assert query.status == "open"
    assert query.confidence == "high"
    assert query.effort is None
    assert query.path_contains == "pkg"
    assert (query.limit, query.offset) == (0, 0)
    assert query.view == "diversified"
    assert query.resolved_order == "queue"
    assert ignored == {
        "refactoring_type": "bogus",
        "status": "nope",
        "effort": "XXL",
        "refactoring_view": "sideways",
        "order": "random",
    }


def test_parse_query_scope_follows_files_and_status() -> None:
    assert parse_query()[0].scope == "fix_first"
    assert parse_query(file_paths=[])[0].scope == "all"
    assert parse_query(file_paths=[])[0].file_paths == ()
    assert parse_query(status="resolved", scope="fix_first")[0].scope == "all"
    assert parse_query(view="canonical")[0].resolved_order == "rank"
    assert parse_query(view="canonical", order="file")[0].resolved_order == "file"


def test_plan_view_maps_the_new_default_to_canonical() -> None:
    assert plan_view(None) == "canonical"
    assert plan_view("diversified") == "canonical"
    assert plan_view("file_spread") == "file_spread"
    assert plan_view("unknown") == "canonical"


def test_evidence_block_names_the_next_cursor() -> None:
    assert evidence_block([{"a": 1}], 1, 0) == {
        "evidence": [{"a": 1}],
        "evidence_total": 1,
        "evidence_emitted": 1,
        "evidence_truncated": False,
    }
    block = evidence_block([{"a": 1}, {"a": 2}], 5, 2)
    assert block["evidence_truncated"] is True
    assert block["evidence_reduced_reason"] == "evidence_page"
    assert block["evidence_next_cursor"] == 4


_TARGET = {
    "file_path": "a.py", "basis": "inferred", "via": "call-graph", "total": 1,
    "tests": ["tests/test_a.py"], "truncated": False,
}
_PROFILE = {
    **_TARGET, "affected_files": ["a.py"], "affected_symbols": [], "commands": [],
}
del _PROFILE["file_path"]


def _row(**over: Any) -> dict[str, Any]:
    details = {
        "steps": [
            {"plan_id": "p1", "file_path": "a.py", "line_start": 3, "line_end": 9,
             "validation_profile_id": "v1"},
            {"plan_id": "p2", "file_path": "a.py"},
        ],
        "evidence": [{"e": 1}, {"e": 2}],
        "rank_factors": {"credit": 1.0},
        "why_ranked": ["x"],
        "file_nloc": 120,
        "validation_profiles": [
            {"id": "v1", **_PROFILE, "targets": [{**_TARGET, "unknown_key": 1}]},
        ],
    }
    row = {
        "opportunity_id": "refop1_a",
        "refactoring_model_version": 1,
        "status": "open",
        "file_path": "a.py",
        "lead_biomarker": "complex_method",
        "lead_refactoring_type": "extract_method",
        "addresses_primary_problem": True,
        "effort_bucket": "S",
        "confidence": "high",
        "step_count": 2,
        "mechanical_steps": 1,
        "judgment_steps": 1,
        "evidence_total": 2,
        "affected_files_total": 1,
        "recoverable_health": 1.23456,
        "rank_score": 0.123456,
        "rank_position": 0,
        "queue_position": 0,
        "details_json": json.dumps(details),
    }
    row.update(over)
    return row


def test_serialize_reads_columns_and_details() -> None:
    payload = serialize(_row(), steps_limit=1, evidence_limit=1)
    assert list(payload)[:3] == ["opportunity_id", "refactoring_model_version", "status"]
    assert payload["recoverable_health"] == 1.235
    assert payload["rank_score"] == 0.1235
    assert payload["file_nloc"] == 120
    assert "dependents" not in payload
    assert payload["steps_total"] == 2
    assert payload["steps_emitted"] == 1
    assert payload["steps_reduced_reason"] == "limit"
    assert payload["evidence_next_cursor"] == 1
    bare = serialize(_row())
    assert "steps" not in bare and "evidence" not in bare


def test_next_actions_end_somewhere() -> None:
    row = _row()
    steps = json.loads(row["details_json"])["steps"]
    tools = [action["tool"] for action in next_actions(row, steps)]
    assert tools == ["get_symbol", "get_risk", "get_health"]
    assert next_actions(row, steps)[0]["arguments"] == {"symbol_id": "a.py:3-9"}
    assert [a["tool"] for a in next_actions(_row(evidence_total=0), [])] == ["get_risk"]


def _summary(lead: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "summary_json": json.dumps({"opportunities_total": 3, "lead": lead}),
        "refactoring_model_version": 1,
        "analyzed_commit": "c" * 40,
    }


def test_summary_and_directive() -> None:
    assert summary_payload(None) == UNAVAILABLE
    assert directive_from_summary(None) == UNAVAILABLE
    payload = summary_payload(_summary(None))
    assert payload["status"] == "available"
    assert payload["analyzed_commit"] == "c" * 40
    clear = directive_from_summary(_summary(None))
    assert clear["status"] == "clear" and clear["opportunities_total"] == 3
    lead = {"opportunity_id": "refop1_a", "file_path": "a.py", "lead_biomarker": "god_class"}
    available = directive_from_summary(_summary({**lead, "addresses_primary_problem": True}))
    assert available["fix_first"] == "a.py"
    assert available["next_action"]["arguments"] == {"opportunity_id": "refop1_a"}
    assert "note" not in available
    assert "do not address 'god_class'" in directive_from_summary(
        _summary({**lead, "addresses_primary_problem": False})
    )["note"]
    assert "unknown rather than no" in directive_from_summary(_summary(lead))["note"]


def test_stored_validation_reads_the_finalizers_profile() -> None:
    validation = stored_validation(_row(), "p1")
    assert validation.basis == "inferred"
    assert [target.file_path for target in validation.targets] == ["a.py"]
    assert stored_validation(_row(), "p2") is None
    assert stored_validation(None, "p1") is None
    assert validation_from_profile({**_PROFILE, "targets": [None], "id": "v9"}).targets == []


def test_plan_payload_prefers_the_public_id() -> None:
    row = {
        "id": "storage",
        "public_id": "refplan_1",
        "refactoring_type": "extract_method",
        "file_path": "a.py",
        "plan_json": json.dumps({"k": 1}),
        "evidence_json": None,
        "blast_radius_json": "[1]",
        "status": "open",
    }
    payload = plan_payload(row)
    assert payload["id"] == "refplan_1"
    assert payload["plan"] == {"k": 1}
    assert payload["evidence"] == {} and payload["blast_radius"] == {}
    assert plan_payload({**row, "public_id": None})["id"] == "storage"


# ---------------------------------------------------------------------------
# One rule table, two readers
# ---------------------------------------------------------------------------

_SEEDS: list[dict[str, Any]] = [
    # Ties on recoverable health and step count, so the secondary key decides.
    {"file_path": "pkg_a/mod_x.py", "recoverable_health": 2.0, "step_count": 3,
     "lead_refactoring_type": "extract_method", "confidence": "high", "effort_bucket": "S",
     "mechanical_steps": 2, "addresses_primary_problem": True, "status": "open"},
    {"file_path": "pkg_a/modAx.py", "recoverable_health": 2.0, "step_count": 1,
     "lead_refactoring_type": "extract_class", "confidence": "medium", "effort_bucket": "M",
     "mechanical_steps": 0, "addresses_primary_problem": False, "status": "open"},
    {"file_path": "pkgb/mod_y.py", "recoverable_health": 5.0, "step_count": 3,
     "lead_refactoring_type": "split_file", "confidence": "low", "effort_bucket": "L",
     "mechanical_steps": 1, "addresses_primary_problem": None, "status": "open"},
    {"file_path": "pkg_a/sub/z.py", "recoverable_health": 0.0, "step_count": 2,
     "lead_refactoring_type": "extract_method", "confidence": "high", "effort_bucket": "S",
     "mechanical_steps": 0, "addresses_primary_problem": True, "status": "open"},
    {"file_path": "other/w.py", "recoverable_health": 1.5, "step_count": 4,
     "lead_refactoring_type": "break_cycle", "confidence": "high", "effort_bucket": "XL",
     "mechanical_steps": 3, "addresses_primary_problem": False, "status": "acknowledged"},
    {"file_path": "other/v.py", "recoverable_health": 1.5, "step_count": 1,
     "lead_refactoring_type": "extract_method", "confidence": "medium", "effort_bucket": "S",
     "mechanical_steps": 1, "addresses_primary_problem": None, "status": "open"},
]
# A queue order that disagrees with rank order, as the diversified one does.
_QUEUE = [3, 0, 5, 1, 4, 2]

_FILTERS: list[dict[str, Any]] = [
    {},
    {"status": "acknowledged"},
    {"lead_types": ["extract_method"]},
    {"lead_types": ["extract_method", "split_file"]},
    {"lead_types": []},
    {"confidence": "high", "effort": "S"},
    {"file_paths": ["pkg_a/mod_x.py", "other/v.py"]},
    {"file_paths": ["other/v.py"]},
    {"file_paths": []},
    # ``_`` is literal, not a wildcard: ``pkg_a`` must not match ``pkgb``.
    {"path_prefix": "pkg_"},
    {"path_contains": "MOD_"},
    {"path_contains": ""},
    {"mechanical_only": True},
    {"addresses_primary": True},
    {"addresses_primary": False},
    {"opportunity_ids": ["refop_0", "refop_2", "refop_5"]},
    {"opportunity_ids": []},
    {"lead_types": ["extract_method"], "mechanical_only": True, "path_prefix": "pkg_a/"},
]


def _seed_rows(repository_id: str) -> list[Any]:
    from repowise.core.persistence.models import RefactoringOpportunity

    return [
        RefactoringOpportunity(
            repository_id=repository_id,
            opportunity_id=f"refop_{i}",
            refactoring_model_version=1,
            rank_position=i,
            queue_position=_QUEUE[i],
            rank_score=1.0 / (i + 1),
            lead_biomarker="complex_method",
            judgment_steps=0,
            evidence_total=0,
            affected_files_total=1,
            details_json="{}",
            **seed,
        )
        for i, seed in enumerate(_SEEDS)
    ]


def _as_mapping(row: Any) -> dict[str, Any]:
    return {column: getattr(row, column) for column in (
        "opportunity_id", "status", "file_path", "lead_refactoring_type", "confidence",
        "effort_bucket", "mechanical_steps", "addresses_primary_problem", "rank_position",
        "queue_position", "recoverable_health", "step_count",
    )}


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


@pytest.mark.parametrize("params", _FILTERS)
@pytest.mark.parametrize("order", [*SORTS, None, "unknown"])
async def test_keep_and_sort_agree_with_the_store(store, params, order) -> None:
    from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
        list_refactoring_opportunities,
        refactoring_opportunity_ids,
    )

    session, repository_id = store
    stored, total = await list_refactoring_opportunities(
        session, repository_id, **params, order=order, limit=100
    )
    expected = [row.opportunity_id for row in stored]
    filters = {"status": "open", **params}
    for rows in (_seed_rows(repository_id), [_as_mapping(r) for r in _seed_rows(repository_id)]):
        kept = sorted(
            (row for row in rows if keep(row, filters)), key=lambda r: row_sort_key(r, order)
        )
        assert [field(row, "opportunity_id") for row in kept] == expected
    assert total == len(expected)
    unpaged = await refactoring_opportunity_ids(
        session, repository_id, **{k: v for k, v in params.items() if k != "opportunity_ids"}
    )
    if "opportunity_ids" not in params:
        assert sorted(unpaged) == sorted(expected)


def test_keep_reads_a_parsed_query() -> None:
    query, _ = parse_query(lead_type="extract_method", mechanical=True)
    rows = [_as_mapping(r) for r in _seed_rows("r")]
    assert [r["opportunity_id"] for r in rows if keep(r, query)] == ["refop_0", "refop_5"]


@pytest.mark.parametrize("status", ["open", "acknowledged", "resolved"])
@pytest.mark.parametrize("ids", [None, ["refop_0", "refop_4"], []])
async def test_facet_counts_agree_with_the_store(store, status, ids) -> None:
    from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
        refactoring_facet_counts,
    )

    session, repository_id = store
    expected = await refactoring_facet_counts(
        session, repository_id, status=status, opportunity_ids=ids
    )
    assert facet_counts(_seed_rows(repository_id), status=status, opportunity_ids=ids) == expected
    assert (
        facet_counts([_as_mapping(r) for r in _seed_rows(repository_id)], status=status,
                     opportunity_ids=ids)
        == expected
    )

"""The decision helpers ``persist_analysis`` runs, called on their own."""

from __future__ import annotations

import pytest

from repowise.core.pipeline.persist import (
    apply_decision_lifecycle,
    repair_decision_store,
    store_decisions,
)
from tests.unit._persist_step_recorders import FAILURE, StepRecorder, install_decision_chain

_STORE = object()


async def test_repairs_run_in_order_and_take_the_vector_store(monkeypatch):
    from repowise.core.analysis.decision_provenance import RETIRED_SOURCES

    rec = StepRecorder(monkeypatch)
    install_decision_chain(rec)

    await repair_decision_store(object(), "repo-1", vector_store=_STORE)

    assert rec.order == [
        "unretire_auto_superseded",
        *["purge_proposed_decisions_by_source"] * len(RETIRED_SOURCES),
        "reconcile_source_ranks",
        "reconcile_decision_confidence",
        "apply_id_migration",
        "apply_migration",
        "prune_unindexed_scope_files",
        "backfill_scope_basis",
        "backfill_session_scope_basis",
        "backfill_decision_node_links",
    ]
    assert rec.args_of("apply_id_migration")[0][1] == {"vector_store": _STORE}


async def test_no_decisions_writes_nothing(monkeypatch):
    rec = StepRecorder(monkeypatch)
    install_decision_chain(rec)

    assert await store_decisions(object(), "repo-1", [], git_metadata=[{"file_path": "a.py"}]) == []
    assert rec.order == []


async def test_store_upserts_then_detects_then_rescores(monkeypatch):
    rec = StepRecorder(monkeypatch)
    install_decision_chain(rec)
    rows = [{"file_path": "src/a.py"}, {"file_path": ""}]

    touched = await store_decisions(
        object(),
        "repo-1",
        [{"title": "t"}],
        vector_store=_STORE,
        git_metadata=rows,
        provider="llm",
    )

    assert touched == ["decision-1"]
    assert rec.order == [
        "bulk_upsert_decisions",
        "detect_supersessions_and_conflicts",
        "recompute_decision_staleness",
    ]
    assert rec.args_of("detect_supersessions_and_conflicts")[0][1]["provider"] == "llm"
    assert rec.args_of("recompute_decision_staleness")[0][0][2] == {
        "src/a.py": {"file_path": "src/a.py"}
    }


async def test_supersession_is_skipped_without_a_vector_store(monkeypatch):
    rec = StepRecorder(monkeypatch)
    install_decision_chain(rec)

    await store_decisions(object(), "repo-1", [{"title": "t"}])

    assert rec.order == ["bulk_upsert_decisions"]


async def test_a_failing_upsert_propagates(monkeypatch):
    rec = StepRecorder(monkeypatch, fail=frozenset({"bulk_upsert_decisions"}))
    install_decision_chain(rec)

    with pytest.raises(RuntimeError, match=FAILURE):
        await store_decisions(object(), "repo-1", [{"title": "t"}], vector_store=_STORE)
    assert rec.order == ["bulk_upsert_decisions"]


async def test_lifecycle_passes_are_isolated(monkeypatch):
    rec = StepRecorder(
        monkeypatch, fail=frozenset({"apply_revert_supersession", "apply_head_artifact_check"})
    )
    install_decision_chain(rec)

    await apply_decision_lifecycle(object(), "repo-1", "/repo")

    assert rec.order == ["apply_revert_supersession", "apply_head_artifact_check"]
    assert rec.args_of("apply_head_artifact_check")[0][0][2] == "/repo"


async def test_failing_passes_after_the_upsert_are_swallowed(monkeypatch):
    rec = StepRecorder(
        monkeypatch,
        fail=frozenset({"detect_supersessions_and_conflicts", "recompute_decision_staleness"}),
    )
    install_decision_chain(rec)

    touched = await store_decisions(
        object(),
        "repo-1",
        [{"title": "t"}],
        vector_store=_STORE,
        git_metadata=[{"file_path": "src/a.py"}],
    )

    assert touched == ["decision-1"]
    assert rec.order == [
        "bulk_upsert_decisions",
        "detect_supersessions_and_conflicts",
        "recompute_decision_staleness",
    ]


async def test_repair_failures_go_to_skip_one_repair_at_a_time(monkeypatch):
    rec = StepRecorder(
        monkeypatch, fail=frozenset({"unretire_auto_superseded", "apply_migration"})
    )
    install_decision_chain(rec)
    skipped: list[tuple[str, str]] = []

    await repair_decision_store(
        object(), "repo-1", skip=lambda step, exc: skipped.append((step, str(exc)))
    )

    assert skipped == [
        ("Decision unretire", FAILURE),
        ("Decision entity migration", FAILURE),
    ]
    assert "reconcile_source_ranks" in rec.order

"""``persist_analysis`` writes its outputs and runs the decision repairs in one order.

The order is load-bearing: un-retiring lands a row at ``proposed``, which the
retired-source purge deletes, so un-retire has to come first; the id migration
precedes everything that reads a record by id; the scope prune precedes the
basis repairs that judge its file list. Every writer is a recorder here.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from repowise.core.pipeline.persist import persist_analysis
from tests.unit._persist_step_recorders import (
    FAILURE,
    StepRecorder,
    install_decision_chain,
)


@dataclass
class _Decision:
    title: str = "Use durable retries"


def _install_analysis_writers(rec: StepRecorder) -> None:
    import repowise.core.pipeline.persist as persist
    from repowise.core.persistence import crud

    for name in (
        "save_dead_code_findings",
        "replace_doc_drift_guarded",
        "save_coverage_files",
        "upsert_git_function_blame_bulk",
        "set_repo_function_mod_p80",
    ):
        rec.patch(crud, name)
    rec.patch(persist, "_analyzed_commit", ret="abc123")
    rec.patch(persist, "save_full_health_report")
    rec.patch(persist, "snapshot_health_from_store")
    install_decision_chain(rec)


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        dead_code_report=SimpleNamespace(findings=[]),
        doc_drift_report=object(),
        health_report=SimpleNamespace(
            coverage_files=["src/a.py"],
            coverage_format="lcov",
            function_blame_rows=[{"symbol_id": "src/a.py::f"}],
            repo_function_mod_p80=3,
        ),
        decision_report=SimpleNamespace(decisions=[_Decision()]),
        vector_store=object(),
        git_metadata_list=[{"file_path": "src/a.py"}],
        repo_path="/repo",
    )


async def test_analysis_writes_then_decision_chain_in_pinned_order(monkeypatch):
    from repowise.core.analysis.decision_provenance import RETIRED_SOURCES

    rec = StepRecorder(monkeypatch)
    _install_analysis_writers(rec)

    await persist_analysis(_result(), object(), "repo-1")

    assert rec.order == [
        "save_dead_code_findings",
        "replace_doc_drift_guarded",
        "_analyzed_commit",
        "save_full_health_report",
        "save_coverage_files",
        "upsert_git_function_blame_bulk",
        "set_repo_function_mod_p80",
        "snapshot_health_from_store",
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
        "bulk_upsert_decisions",
        "detect_supersessions_and_conflicts",
        "recompute_decision_staleness",
        "apply_revert_supersession",
        "apply_head_artifact_check",
        "refresh_governance_findings",
    ]
    assert [a[2] for a, _k in rec.args_of("purge_proposed_decisions_by_source")] == list(
        RETIRED_SOURCES
    )


async def test_each_decision_repair_is_isolated_from_the_others(monkeypatch):
    """A failing repair skips only itself (or its own group), never the chain."""
    fail = frozenset(
        {
            "unretire_auto_superseded",
            "purge_proposed_decisions_by_source",
            "reconcile_source_ranks",
            "reconcile_decision_confidence",
            "apply_id_migration",
            "apply_migration",
            "detect_supersessions_and_conflicts",
            "recompute_decision_staleness",
            "apply_revert_supersession",
            "apply_head_artifact_check",
            "refresh_governance_findings",
        }
    )
    rec = StepRecorder(monkeypatch, fail=fail)
    _install_analysis_writers(rec)

    await persist_analysis(_result(), object(), "repo-1")

    assert rec.order[rec.order.index("unretire_auto_superseded") :] == [
        "unretire_auto_superseded",
        # The purge loop shares one try: the first failure ends the loop.
        "purge_proposed_decisions_by_source",
        "reconcile_source_ranks",
        "reconcile_decision_confidence",
        "apply_id_migration",
        # The migration group shares one try too.
        "apply_migration",
        "bulk_upsert_decisions",
        "detect_supersessions_and_conflicts",
        "recompute_decision_staleness",
        "apply_revert_supersession",
        "apply_head_artifact_check",
        "refresh_governance_findings",
    ]


async def test_bulk_upsert_failure_is_not_swallowed(monkeypatch):
    rec = StepRecorder(monkeypatch, fail=frozenset({"bulk_upsert_decisions"}))
    _install_analysis_writers(rec)

    with pytest.raises(RuntimeError, match=FAILURE):
        await persist_analysis(_result(), object(), "repo-1")
    assert rec.order[-1] == "bulk_upsert_decisions"

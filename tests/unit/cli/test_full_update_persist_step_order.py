"""The docs-mode update persister runs its steps in one pinned order.

``_persist_full_update_async`` mirrors the index-only persister and adds the
docs-only steps (pages, stale decay, decisions, generation job, FTS indexing).
Every step is a recorder here, so these tests prove a refactor kept the
sequence and the degraded entries, and that the steps both persisters share
receive the same inputs from the same update.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.cli.commands.update_cmd.persistence import _persist_full_update_async
from repowise.core.pipeline.incremental import persist_incremental_index
from tests.unit._persist_step_recorders import (
    FAILURE,
    GIT_META,
    REFUSAL,
    SWEPT,
    TOMBSTONED,
    StepRecorder,
    dead_code_report,
    file_diffs,
    graph_builder,
    install_decision_chain,
    install_shared_steps,
    parsed_files,
)
from tests.unit.cli.test_update_persist_reliability import _page


@dataclass
class _Marker:
    title: str = "Use durable retries"


HAPPY_ORDER = [
    "reconcile_full_index_scope",
    "upsert_pages_from_generated",
    "tombstone_pages_outside_generation",
    "sweep_retired_pages",
    "sweep_absent_cycle_pages",
    "sweep_absent_symbol_spotlights",
    "sweep_deselected_pages",
    # Swept embeddings leave before the SQL session commits.
    "vectors.delete_many",
    "tombstone_candidates",
    "mark_tombstone_pages",
    "file_import_edges",
    "backfill_related_pages",
    "mark_page_ids_stale",
    "rebuild_page_tree",
    "persist_kg",
    "persist_git_refresh",
    "persist_incremental_commits",
    "unretire_auto_superseded",
    "purge_proposed_decisions_by_source",
    "purge_proposed_decisions_by_source",
    "purge_proposed_decisions_by_source",
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
    "replace_doc_drift_guarded",
    "persist_graph_nodes",
    "persist_partial_health",
    "replace_dead_code_findings",
    "persist_incremental_symbols",
    "persist_incremental_security_findings",
    "persist_incremental_edges",
    "refresh_external_systems",
    "prune_deleted_file_rows",
    "_analyzed_commit",
    "finalize_performance_opportunities",
    "finalize_refactoring_opportunities",
    "upsert_generation_job",
    # After the session closes.
    "fts.ensure_index",
    "fts.index_pages",
    "fts.delete_many",
    "vectors.delete_many",
    "refresh_commit_health_after_update",
]


def _install(rec: StepRecorder, *, prune_ret: tuple[int, list[str]] = (1, [])) -> None:
    import repowise.core.generation as generation
    import repowise.core.generation.cascade as cascade
    import repowise.core.generation.scope as scope
    import repowise.core.persistence as persistence
    import repowise.core.pipeline.persist as persist
    import repowise.core.pipeline.scoped_generation as scoped_generation
    import repowise.core.repo_config as repo_config
    from repowise.core.persistence import crud

    install_shared_steps(rec, prune_ret=prune_ret)
    install_decision_chain(rec)
    rec.patch(persistence, "upsert_pages_from_generated")
    rec.patch(persist, "mark_page_ids_stale")
    rec.patch(crud, "upsert_generation_job", ret=SimpleNamespace())

    # Inputs of the stale decay, not steps: stubbed without recording.
    async def _no_rows(_session, _repo_id):
        return []

    mp = rec.monkeypatch
    mp.setattr(repo_config, "load_repo_config", lambda _path: {})
    mp.setattr(generation.GenerationConfig, "from_repo_config", lambda _cfg: object())
    mp.setattr(scoped_generation, "_load_page_rows", _no_rows)
    mp.setattr(scoped_generation, "load_kg_context", lambda _path: object())
    mp.setattr(scope, "load_page_records", lambda _rows: [])
    mp.setattr(scope, "build_dependencies", lambda **_kwargs: object())
    mp.setattr(cascade, "expand_cascade", lambda *_a: SimpleNamespace(stale_ids=set()))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    return tmp_path


class _Inputs:
    """One update's worth of inputs, shared so both persisters see the same objects."""

    def __init__(self) -> None:
        self.graph_builder = graph_builder()
        self.parsed_files = parsed_files()
        self.file_diffs = file_diffs()
        self.dead_code_report = dead_code_report()
        self.health_report = object()
        self.doc_drift_report = object()
        self.knowledge_graph_result = object()
        self.pages = [_page("file_page:src/a.py")]


async def _persist_full(
    repo: Path,
    rec: StepRecorder,
    inputs: _Inputs,
    degraded: list[str],
    failed_steps: list[str] | None = None,
) -> int:
    return await _persist_full_update_async(
        repo_path=repo,
        repo_name=repo.name,
        generated_pages=inputs.pages,
        file_diffs=inputs.file_diffs,
        git_meta_map=dict(GIT_META),
        new_decision_markers=[_Marker()],
        decision_vector_store=rec.vector_store(),
        provider=None,
        partial_health_report=inputs.health_report,
        dead_code_report=inputs.dead_code_report,
        graph_builder=inputs.graph_builder,
        knowledge_graph_result=inputs.knowledge_graph_result,
        degraded=degraded,
        doc_drift_report=inputs.doc_drift_report,
        decay_paths=["src/a.py"],
        parsed_files=inputs.parsed_files,
        reconcile_full_scope=True,
        reconcile_full_generation=True,
        failed_steps=failed_steps,
    )


async def test_every_step_runs_in_the_pinned_order(repo, monkeypatch):
    rec = StepRecorder(monkeypatch)
    _install(rec)
    degraded: list[str] = []

    total = await _persist_full(repo, rec, _Inputs(), degraded)

    rec.assert_all_called(HAPPY_ORDER)
    assert rec.order == HAPPY_ORDER
    assert degraded == []
    # The page upsert is a recorder, so the store holds no page to count.
    assert total == 0
    assert [a[2] for a, _k in rec.args_of("purge_proposed_decisions_by_source")] == [
        "code_comment",
        "readme_mining",
        "changelog",
    ]
    assert rec.args_of("vectors.delete_many") == [(([SWEPT],), {}), (([TOMBSTONED],), {})]
    assert rec.args_of("fts.delete_many") == [((sorted([SWEPT, TOMBSTONED]),), {})]


# Steps left working when every other one fails.
_SURVIVORS = {
    # Pages have no net: a failure here fails the update.
    "upsert_pages_from_generated",
    "tombstone_candidates",
    "file_import_edges",
    "prune_deleted_file_rows",
    "_analyzed_commit",
    "refresh_commit_health_after_update",
}


async def test_every_failing_step_degrades_in_order(repo, monkeypatch):
    from repowise.core.pipeline.cleanup_debt import record_cleanup_debt

    # Seeded debt is what reaches the vector tail once tombstoning has failed.
    record_cleanup_debt(repo, "vectors", {"file_page:src/old.py"})
    rec = StepRecorder(monkeypatch, fail=frozenset(HAPPY_ORDER) - _SURVIVORS)
    _install(rec, prune_ret=(1, [REFUSAL]))
    degraded: list[str] = []

    await _persist_full(repo, rec, _Inputs(), degraded)

    assert rec.order == [
        "reconcile_full_index_scope",
        "upsert_pages_from_generated",
        "tombstone_pages_outside_generation",
        "sweep_retired_pages",
        "tombstone_candidates",
        "mark_tombstone_pages",
        "file_import_edges",
        "backfill_related_pages",
        "mark_page_ids_stale",
        "rebuild_page_tree",
        "persist_kg",
        "persist_git_refresh",
        "persist_incremental_commits",
        # Each repair fails on its own; the upsert still runs.
        "unretire_auto_superseded",
        "purge_proposed_decisions_by_source",
        "reconcile_source_ranks",
        "reconcile_decision_confidence",
        "apply_id_migration",
        "apply_migration",
        "bulk_upsert_decisions",
        "apply_revert_supersession",
        "apply_head_artifact_check",
        "refresh_governance_findings",
        "replace_doc_drift_guarded",
        "persist_graph_nodes",
        "persist_partial_health",
        "replace_dead_code_findings",
        "persist_incremental_symbols",
        "persist_incremental_security_findings",
        "persist_incremental_edges",
        "refresh_external_systems",
        "prune_deleted_file_rows",
        "_analyzed_commit",
        "finalize_performance_opportunities",
        "upsert_generation_job",
        "fts.ensure_index",
        "vectors.delete_many",
        "refresh_commit_health_after_update",
    ]
    assert degraded == [
        *(
            f"{step}: {FAILURE}"
            for step in (
                "Config scope reconciliation",
                "Generation scope reconciliation",
                "Retired page sweep",
                "Tombstone marking",
                "Related-pages backfill",
                "Stale-page decay",
                "Page tree rebuild",
                "Knowledge-graph persist",
                "Git persist",
                "Commit capture",
                "Decision unretire",
                "Retired-source decision purge",
                "Decision source-rank repair",
                "Decision confidence repair",
                "Decision id migration",
                "Decision entity migration",
                "Decision persist",
                "Revert supersession",
                "Decision HEAD artifact check",
                "Governance findings",
                "Doc-drift persist",
                "Graph nodes persist",
                "Health persist",
                "Dead-code persist",
                "Symbol persist",
                "Security persist",
                "Graph edges persist",
                "External systems refresh",
            )
        ),
        REFUSAL,
        f"Queue rebuild after prune: {FAILURE}",
        f"Generation job record: {FAILURE}",
        f"Full-text search indexing: {FAILURE}",
        f"Deferred vector cleanup: {FAILURE}",
    ]


async def test_range_scoped_failures_are_recorded_for_repair(repo, monkeypatch):
    rec = StepRecorder(monkeypatch, fail=frozenset(HAPPY_ORDER) - _SURVIVORS)
    _install(rec)
    failed_steps: list[str] = []

    await _persist_full(repo, rec, _Inputs(), [], failed_steps)

    assert failed_steps == [
        "Tombstone marking",
        "Git persist",
        "Health persist",
        "Dead-code persist",
        "Symbol persist",
        "Security persist",
        "Graph edges persist",
        "External systems refresh",
        "Full-text search indexing",
    ]


async def test_a_failing_decision_repair_skips_only_itself(repo, monkeypatch):
    rec = StepRecorder(monkeypatch, fail=frozenset({"unretire_auto_superseded"}))
    _install(rec)
    degraded: list[str] = []

    await _persist_full(repo, rec, _Inputs(), degraded)

    rec.assert_all_called(HAPPY_ORDER)
    assert rec.order == HAPPY_ORDER
    assert degraded == [f"Decision unretire: {FAILURE}"]


async def test_a_failing_repair_fails_a_required_decision_persist(repo, monkeypatch):
    rec = StepRecorder(monkeypatch, fail=frozenset({"unretire_auto_superseded"}))
    _install(rec)
    degraded: list[str] = []
    inputs = _Inputs()

    with pytest.raises(RuntimeError, match=FAILURE):
        await _persist_full_update_async(
            repo_path=repo,
            repo_name=repo.name,
            generated_pages=inputs.pages,
            file_diffs=inputs.file_diffs,
            git_meta_map=dict(GIT_META),
            new_decision_markers=[],
            decision_vector_store=rec.vector_store(),
            provider=None,
            partial_health_report=None,
            dead_code_report=None,
            graph_builder=inputs.graph_builder,
            knowledge_graph_result=None,
            degraded=degraded,
            parsed_files=inputs.parsed_files,
            require_decision_persist_success=True,
        )

    assert degraded == [f"Decision unretire: {FAILURE}"]
    assert "purge_proposed_decisions_by_source" not in rec.order


# ---------------------------------------------------------------------------
# Parity: the shared middle steps get the same inputs from both persisters
# ---------------------------------------------------------------------------

# Steps both persisters call. Each pair of lists below must match exactly.
SHARED_STEPS = [
    "reconcile_full_index_scope",
    "tombstone_pages_outside_generation",
    "sweep_retired_pages",
    "sweep_absent_cycle_pages",
    "sweep_absent_symbol_spotlights",
    "sweep_deselected_pages",
    "tombstone_candidates",
    "mark_tombstone_pages",
    "rebuild_page_tree",
    "persist_git_refresh",
    "persist_incremental_commits",
    "replace_doc_drift_guarded",
    "persist_graph_nodes",
    "persist_partial_health",
    "persist_incremental_symbols",
    "persist_incremental_security_findings",
    "persist_incremental_edges",
    "file_import_edges",
    "persist_kg",
    "refresh_external_systems",
    "prune_deleted_file_rows",
    "finalize_performance_opportunities",
    "finalize_refactoring_opportunities",
    "refresh_commit_health_after_update",
]


def _normalize(value: Any) -> Any:
    # Each run opens its own session; the repo row (keyed by path) is shared.
    name = type(value).__name__
    if name == "AsyncSession":
        return "<session>"
    if name == "async_sessionmaker":
        return "<session factory>"
    if isinstance(value, (list, tuple)):
        return type(value)(_normalize(v) for v in value)
    return value


def _calls(rec: StepRecorder, label: str) -> list[tuple[Any, dict]]:
    # ``timings`` and ``log`` are observability, not data.
    return [
        (
            _normalize(args),
            {k: _normalize(v) for k, v in kwargs.items() if k not in {"timings", "log"}},
        )
        for args, kwargs in rec.args_of(label)
    ]


async def test_shared_steps_receive_the_same_inputs_from_both_persisters(repo, monkeypatch):
    inputs = _Inputs()

    core = StepRecorder(monkeypatch)
    _install(core)
    await persist_incremental_index(
        repo,
        inputs.graph_builder,
        dict(GIT_META),
        inputs.dead_code_report,
        inputs.health_report,
        [fd.path for fd in inputs.file_diffs],
        doc_drift_report=inputs.doc_drift_report,
        file_diffs=inputs.file_diffs,
        knowledge_graph_result=inputs.knowledge_graph_result,
        parsed_files=inputs.parsed_files,
        reconcile_full_scope=True,
        full_generation_page_ids={p.page_id for p in inputs.pages},
        vector_store=core.vector_store(),
        log=lambda _msg: None,
        degraded=[],
        failed_steps=[],
    )

    cli = StepRecorder(monkeypatch)
    _install(cli)
    await _persist_full(repo, cli, inputs, [])

    for label in SHARED_STEPS:
        assert _calls(core, label), label
        assert _calls(core, label) == _calls(cli, label), label

    # Known divergences, pinned so a unification shows up as a test change.
    # Dead code: core hands over the finding objects, the CLI their dicts.
    ((core_args, core_kwargs),) = _calls(core, "replace_dead_code_findings")
    ((cli_args, cli_kwargs),) = _calls(cli, "replace_dead_code_findings")
    assert core_args[:2] == cli_args[:2] and core_kwargs == cli_kwargs
    # Related pages: the CLI skips the pages this run just generated.
    ((_a, core_rel),) = _calls(core, "backfill_related_pages")
    ((_a, cli_rel),) = _calls(cli, "backfill_related_pages")
    assert cli_rel.pop("skip_page_ids") == {"file_page:src/a.py"}
    assert core_rel == cli_rel
    # Decisions: core drains only code_comment, the CLI every retired source.
    assert [a[2] for a, _k in _calls(core, "purge_proposed_decisions_by_source")] == [
        "code_comment"
    ]

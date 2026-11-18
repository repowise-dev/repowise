"""``persist_incremental_index`` runs its steps in one pinned order.

Characterization, not a spec: every step is replaced by a recorder, so these
tests prove a refactor of the persister kept the sequence, the degraded
entries and the repair markers exactly as they were.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
    install_shared_steps,
    parsed_files,
)

HAPPY_ORDER = [
    "reconcile_full_index_scope",
    "tombstone_pages_outside_generation",
    "restore_stored_complexity",
    "sweep_store_hygiene",
    "sweep_absent_cycle_pages",
    "sweep_absent_symbol_spotlights",
    "sweep_deselected_pages",
    "tombstone_candidates",
    "mark_tombstone_pages",
    "rebuild_page_tree",
    "persist_git_refresh",
    "persist_incremental_commits",
    "replace_dead_code_findings",
    "replace_doc_drift_guarded",
    "persist_graph_nodes",
    "persist_partial_health",
    "persist_incremental_symbols",
    "persist_incremental_security_findings",
    "persist_incremental_edges",
    "file_import_edges",
    "backfill_related_pages",
    "persist_kg",
    "refresh_external_systems",
    "purge_proposed_decisions_by_source",
    "prune_deleted_file_rows",
    "prune_absent_synthetic_nodes",
    "_analyzed_commit",
    "finalize_performance_opportunities",
    "finalize_refactoring_opportunities",
    "apply_fix_rollups",
    # After the session closes.
    "refresh_commit_health_after_update",
    "fts.ensure_index",
    "fts.delete_many",
    "vectors.delete_many",
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    return tmp_path


async def _persist(repo: Path, rec: StepRecorder, **overrides) -> tuple[list[str], list[str]]:
    degraded: list[str] = []
    failed_steps: list[str] = []
    kwargs = {
        "reconcile_full_scope": True,
        "full_generation_page_ids": {"file_page:src/a.py"},
        "file_diffs": file_diffs(),
        "doc_drift_report": object(),
        "knowledge_graph_result": object(),
        "parsed_files": parsed_files(),
        "vector_store": rec.vector_store(),
        "log": lambda _msg: None,
        "degraded": degraded,
        "failed_steps": failed_steps,
    }
    kwargs.update(overrides)
    await persist_incremental_index(
        repo,
        graph_builder(),
        dict(GIT_META),
        dead_code_report(),
        object(),
        ["src/a.py"],
        **kwargs,
    )
    return degraded, failed_steps


async def test_every_step_runs_in_the_pinned_order(repo, monkeypatch):
    rec = StepRecorder(monkeypatch)
    install_shared_steps(rec)

    degraded, failed_steps = await _persist(repo, rec)

    rec.assert_all_called(HAPPY_ORDER)
    assert rec.order == HAPPY_ORDER
    assert degraded == []
    assert failed_steps == []
    # The incremental path only drains the retired code_comment source.
    assert [a[2] for a, _k in rec.args_of("purge_proposed_decisions_by_source")] == ["code_comment"]
    # Tombstones and swept pages leave FTS. The vectors lose the tombstones and
    # the hygiene sweep's pages, which include embedded file-keyed pages.
    assert rec.args_of("fts.delete_many") == [((sorted([SWEPT, TOMBSTONED]),), {})]
    assert rec.args_of("vectors.delete_many") == [((sorted([SWEPT, TOMBSTONED]),), {})]
    ((_args, git_kwargs),) = rec.args_of("persist_git_refresh")
    assert git_kwargs == {"keep_commit_health": True, "gone_paths": set()}


async def test_full_history_walk_skips_commit_capture_and_keeps_archaeology(repo, monkeypatch):
    rec = StepRecorder(monkeypatch)
    install_shared_steps(rec)

    await _persist(repo, rec, full_git_summary=object(), history_window_changed=True)

    assert "persist_incremental_commits" not in rec.order
    ((_args, git_kwargs),) = rec.args_of("persist_git_refresh")
    assert git_kwargs == {"keep_commit_health": False, "gone_paths": set()}
    # Nothing on this path re-mines git archaeology, so its records stay.
    assert [a[2] for a, _k in rec.args_of("purge_proposed_decisions_by_source")] == [
        "code_comment"
    ]


@pytest.mark.parametrize("enabled", [False, True])
async def test_graph_seed_persist_follows_the_switch(repo, monkeypatch, enabled: bool) -> None:
    import repowise.core.pipeline.incremental.graph_seed as graph_seed

    calls = []

    async def record_save(repo_path, builder):
        calls.append((repo_path, builder))

    monkeypatch.setattr(graph_seed, "save_seed_edge_cache", record_save)
    if enabled:
        monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", "1")
    else:
        monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", "0")
    rec = StepRecorder(monkeypatch)
    install_shared_steps(rec)

    await _persist(repo, rec)

    if enabled:
        assert len(calls) == 1
        assert calls[0][0] == repo
        assert calls[0][1].graph().has_node("src/a.py")
    else:
        assert calls == []


async def test_every_failing_step_degrades_and_only_range_scoped_ones_are_marked(repo, monkeypatch):
    from repowise.core.pipeline.cleanup_debt import record_cleanup_debt

    # Seeded debt is what reaches the cleanup tail once tombstoning has failed.
    record_cleanup_debt(repo, "fts", {"file_page:src/old.py"})
    record_cleanup_debt(repo, "vectors", {"file_page:src/old.py"})
    survivors = {
        "tombstone_candidates",
        "file_import_edges",
        "prune_deleted_file_rows",
        "_analyzed_commit",
        # Not wrapped: a failure here propagates.
        "refresh_commit_health_after_update",
    }
    fail = frozenset(label for label in HAPPY_ORDER if label not in survivors)
    rec = StepRecorder(monkeypatch, fail=fail)
    install_shared_steps(rec, prune_ret=(1, [REFUSAL]))

    degraded, failed_steps = await _persist(repo, rec)

    assert rec.order == [
        "reconcile_full_index_scope",
        "tombstone_pages_outside_generation",
        "restore_stored_complexity",
        "sweep_store_hygiene",
        "tombstone_candidates",
        "mark_tombstone_pages",
        "rebuild_page_tree",
        "persist_git_refresh",
        "persist_incremental_commits",
        "replace_dead_code_findings",
        "replace_doc_drift_guarded",
        "persist_graph_nodes",
        "persist_partial_health",
        "persist_incremental_symbols",
        "persist_incremental_security_findings",
        "persist_incremental_edges",
        "file_import_edges",
        "backfill_related_pages",
        "persist_kg",
        "refresh_external_systems",
        "purge_proposed_decisions_by_source",
        "prune_deleted_file_rows",
        "prune_absent_synthetic_nodes",
        "_analyzed_commit",
        "finalize_performance_opportunities",
        "apply_fix_rollups",
        "refresh_commit_health_after_update",
        "fts.ensure_index",
        "vectors.delete_many",
    ]
    assert degraded == [
        *(
            f"{step}: {FAILURE}"
            for step in (
                "Config scope reconciliation",
                "Generation scope reconciliation",
                "Stored complexity restore",
                "Retired page sweep",
                "Tombstone marking",
                "Page tree rebuild",
                "Git persist",
                "Commit capture",
                "Dead-code persist",
                "Doc-drift persist",
                "Graph nodes persist",
                "Health persist",
                "Symbol persist",
                "Security persist",
                "Graph edges persist",
                "Related-pages backfill",
                "Knowledge-graph persist",
                "External systems refresh",
                "Decision purge",
            )
        ),
        # A prune refusal is reported verbatim, not as a skipped step.
        REFUSAL,
        f"Synthetic graph node prune: {FAILURE}",
        f"Queue rebuild after prune: {FAILURE}",
        f"Git rollups: {FAILURE}",
        f"Tombstone full-text removal: {FAILURE}",
        f"Tombstone vector removal: {FAILURE}",
    ]
    assert failed_steps == [
        "Tombstone marking",
        "Git persist",
        "Dead-code persist",
        "Health persist",
        "Symbol persist",
        "Security persist",
        "Graph edges persist",
        "External systems refresh",
        "Tombstone full-text removal",
    ]


def test_deleted_paths_are_keyed_from_the_git_root(tmp_path: Path) -> None:
    """git_metadata keys on the git root, also when the index covers a subdirectory."""
    import subprocess
    from types import SimpleNamespace

    from repowise.core.pipeline.incremental.persist_index import IncrementalPersist, _deleted_paths

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "kept.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ctx = IncrementalPersist(
        repo_path=tmp_path / "pkg",
        graph_builder=None,
        git_meta_map={},
        file_diffs=[
            SimpleNamespace(path="gone.py", status="deleted"),
            SimpleNamespace(path="new.py", status="renamed", old_path="kept.py"),
            SimpleNamespace(path="moved.py", status="renamed", old_path="old.py"),
        ],
    )

    assert _deleted_paths(ctx) == {"pkg/gone.py", "pkg/old.py"}


async def test_complexity_is_restored_outside_the_diffed_files(repo, monkeypatch):
    # A traversal change persists every file, but partial health measured only the diff.
    rec = StepRecorder(monkeypatch)
    install_shared_steps(rec)

    await persist_incremental_index(
        repo,
        graph_builder(),
        dict(GIT_META),
        dead_code_report(),
        object(),
        ["src/a.py", "src/b.py"],
        file_diffs=file_diffs(),
        parsed_files=parsed_files(),
        log=lambda _msg: None,
        degraded=[],
        failed_steps=[],
    )

    [(args, _kwargs)] = rec.args_of("restore_stored_complexity")
    assert args[-1] == ["src/a.py"]

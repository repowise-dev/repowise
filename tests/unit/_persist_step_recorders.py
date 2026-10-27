"""Recorders for the steps the update persisters call, shared by the step-order tests.

Each step is replaced, in the module namespace it is looked up in at call
time, by a stand-in that appends its label to one shared call log and can be
told to raise. Most steps are imported inside the function body, so patching
the defining module is enough; a step imported at module level is also patched
at every module that re-exports it (``where=``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import networkx as nx

FAILURE = "boom"


class StepRecorder:
    def __init__(self, monkeypatch: Any, *, fail: frozenset[str] = frozenset()) -> None:
        self.monkeypatch = monkeypatch
        self.fail = fail
        self.calls: list[tuple[str, tuple, dict]] = []

    @property
    def order(self) -> list[str]:
        return [label for label, _args, _kwargs in self.calls]

    def args_of(self, label: str) -> list[tuple[tuple, dict]]:
        return [(args, kwargs) for name, args, kwargs in self.calls if name == label]

    def _record(self, label: str, args: tuple, kwargs: dict, ret: Any) -> Any:
        self.calls.append((label, args, kwargs))
        if label in self.fail:
            raise RuntimeError(FAILURE)
        # A fresh copy: callers extend returned lists in place.
        return list(ret) if isinstance(ret, list) else ret

    def assert_all_called(self, labels: Any) -> None:
        """Fail on any label that never ran, i.e. a patch that missed its lookup site."""
        missing = [label for label in labels if label not in self.order]
        assert not missing, f"steps never called: {missing}"

    def patch(
        self,
        module: Any,
        attr: str,
        *,
        ret: Any = None,
        sync: bool = False,
        where: tuple[Any, ...] = (),
    ) -> None:
        """Replace ``attr`` on its defining ``module`` and on every module in ``where``."""
        if sync:

            def stand_in(*args, **kwargs):
                return self._record(attr, args, kwargs, ret)

        else:

            async def stand_in(*args, **kwargs):
                return self._record(attr, args, kwargs, ret)

        for target in (module, *where):
            self.monkeypatch.setattr(target, attr, stand_in)

    def fts_class(self) -> type:
        rec = self

        class _FTS:
            def __init__(self, _engine: Any) -> None:
                pass

            async def ensure_index(self) -> None:
                rec._record("fts.ensure_index", (), {}, None)

            async def index_pages(self, pages: Any) -> None:
                rec._record("fts.index_pages", (list(pages),), {}, None)

            async def delete_many(self, page_ids: Any) -> None:
                rec._record("fts.delete_many", (list(page_ids),), {}, None)

        return _FTS

    def vector_store(self) -> Any:
        rec = self

        class _Vectors:
            async def delete_many(self, ids: Any) -> None:
                rec._record("vectors.delete_many", (list(ids),), {}, None)

        return _Vectors()


# Returned ids are what make the post-transaction cleanup run.
TOMBSTONED = "file_page:src/gone.py"
SWEPT = "onboarding:retired"
REFUSAL = "Refused to prune src/kept.py"


def install_shared_steps(rec: StepRecorder, *, prune_ret: tuple[int, list[str]] = (1, [])) -> None:
    """Steps both persisters call: the incremental list and its cleanup tail."""
    import repowise.core.generation.related_pages as related_pages
    import repowise.core.persistence as persistence
    import repowise.core.persistence.search as search
    import repowise.core.pipeline.commit_health as commit_health
    import repowise.core.pipeline.incremental as incremental
    import repowise.core.pipeline.incremental.commits as commits
    import repowise.core.pipeline.incremental.external_systems as external_systems
    import repowise.core.pipeline.incremental.health as health
    import repowise.core.pipeline.incremental.persist_index as persist_index
    import repowise.core.pipeline.page_tree_sync as page_tree_sync
    import repowise.core.pipeline.persist as persist
    from repowise.core.persistence import crud

    rec.patch(persist, "reconcile_full_index_scope", ret=[])
    rec.patch(persist, "tombstone_pages_outside_generation", ret=[])
    rec.patch(persist, "sweep_retired_pages", ret=[SWEPT])
    rec.patch(persist, "sweep_absent_cycle_pages", ret=[])
    rec.patch(persist, "sweep_absent_symbol_spotlights", ret=[])
    rec.patch(persist, "sweep_deselected_pages", ret=[])
    # One value for both persisters, so their sweep inputs compare equal.
    rec.monkeypatch.setattr(persist_index, "_generation_config", lambda _path: "config")
    rec.patch(persist, "tombstone_candidates", ret=[], sync=True)
    rec.patch(persist, "mark_tombstone_pages", ret=[TOMBSTONED])
    rec.patch(page_tree_sync, "rebuild_page_tree")
    rec.patch(persist, "persist_git_refresh")
    # Package callers look these up at call time, the core writer on its own module.
    reexported = (incremental, persist_index)
    rec.patch(commits, "persist_incremental_commits", where=reexported)
    rec.patch(crud, "replace_dead_code_findings")
    rec.patch(crud, "replace_doc_drift_guarded")
    rec.patch(persist, "persist_graph_nodes")
    rec.patch(health, "persist_partial_health", where=reexported)
    rec.patch(persist, "persist_incremental_symbols")
    rec.patch(persist, "persist_incremental_security_findings")
    rec.patch(persist, "persist_incremental_edges")
    rec.patch(related_pages, "file_import_edges", ret={}, sync=True)
    rec.patch(crud, "backfill_related_pages", ret=0)
    rec.patch(persist, "persist_kg")
    rec.patch(external_systems, "refresh_external_systems", where=reexported)
    rec.patch(crud, "purge_proposed_decisions_by_source")
    rec.patch(persist, "prune_deleted_file_rows", ret=prune_ret)
    rec.patch(persist, "_analyzed_commit", ret="abc123")
    rec.patch(crud, "finalize_performance_opportunities")
    rec.patch(crud, "finalize_refactoring_opportunities")
    rec.patch(commit_health, "refresh_commit_health_after_update")
    # Core reads it from the search module, the CLI from the package.
    fts = rec.fts_class()
    rec.monkeypatch.setattr(search, "FullTextSearch", fts)
    rec.monkeypatch.setattr(persistence, "FullTextSearch", fts)


def install_decision_chain(rec: StepRecorder) -> None:
    """The decision repairs shared by ``persist_analysis`` and the CLI full update."""
    import repowise.core.analysis.decision_evolution as decision_evolution
    import repowise.core.analysis.decisions.head_artifacts as head_artifacts
    import repowise.core.analysis.decisions.reverts as reverts
    import repowise.core.persistence.decision_id_migration as decision_id_migration
    import repowise.core.persistence.decision_migration as decision_migration
    import repowise.core.pipeline.persist as persist
    from repowise.core.persistence import crud

    rec.patch(crud, "unretire_auto_superseded")
    rec.patch(crud, "purge_proposed_decisions_by_source")
    rec.patch(crud, "reconcile_source_ranks")
    rec.patch(crud, "reconcile_decision_confidence")
    rec.patch(decision_id_migration, "apply_id_migration")
    for name in (
        "apply_migration",
        "prune_unindexed_scope_files",
        "backfill_scope_basis",
        "backfill_session_scope_basis",
        "backfill_decision_node_links",
    ):
        rec.patch(decision_migration, name)
    rec.patch(crud, "bulk_upsert_decisions", ret=["decision-1"])
    rec.patch(
        decision_evolution,
        "detect_supersessions_and_conflicts",
        ret={"superseded": 0, "conflicts": 0},
    )
    rec.patch(crud, "recompute_decision_staleness", ret=0)
    rec.patch(reverts, "apply_revert_supersession")
    rec.patch(head_artifacts, "apply_head_artifact_check")
    rec.patch(persist, "refresh_governance_findings")


def graph_builder() -> Any:
    graph = nx.DiGraph()
    graph.add_node("src/a.py", node_type="file")
    return SimpleNamespace(
        graph=lambda: graph,
        pagerank=lambda: {"src/a.py": 1.0},
        traversed_file_paths={"src/a.py"},
    )


def parsed_files() -> list[Any]:
    return [SimpleNamespace(file_info=SimpleNamespace(path="src/a.py"))]


def file_diffs() -> list[Any]:
    from repowise.core.ingestion.change_detector import FileDiff

    return [FileDiff("src/a.py", "modified", None, None, None, None)]


def dead_code_report() -> Any:
    return SimpleNamespace(findings=[], authoritative_paths={"src/a.py"})


GIT_META = {"src/a.py": {"file_path": "src/a.py"}}

"""Partial health, dead-code and doc-drift analysis for an incremental run."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.pipeline.phase_timing import PhaseTimings, timed

from ._common import LogFn, _noop_log


def _performance_only_config(config: dict | None, performance_only: set[str]) -> dict | None:
    """Disable every non-performance detector on the closure's files.

    The closure adds files whose call paths reach a changed sink. Only their
    performance findings survive the filter below; every other detector's
    output for them is computed and discarded, and on a central helper the
    closure runs to hundreds of files. The walk still covers them, because the
    performance detectors read it.
    """
    if not performance_only:
        return config
    from repowise.core.analysis.health.biomarkers import registered_biomarkers
    from repowise.core.analysis.health.scoring import dimensions_for

    non_performance = {
        b.name for b in registered_biomarkers() if "performance" not in dimensions_for(b.name)
    }
    out = dict(config or {})
    per_file = {k: set(v) for k, v in (out.get("per_file_disabled") or {}).items()}
    for path in performance_only:
        per_file[path] = per_file.get(path, set()) | non_performance
    out["per_file_disabled"] = per_file
    return out


def run_partial_analysis(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    parsed_files: list,
    file_diffs: list,
    *,
    source_map: dict[str, bytes] | None = None,
    stored_git_meta: dict[str, dict] | None = None,
    stored_performance_callers: set[str] | None = None,
    repo_function_mod_p80: int | None = None,
    coverage_map: dict[str, dict] | None = None,
    log: LogFn | None = None,
    timings: PhaseTimings | None = None,
    dead_code: bool = True,
) -> tuple[Any, Any]:
    """Run partial code-health + repo-wide dead-code analysis.

    Returns ``(partial_health_report, dead_code_report)`` — either may be
    ``None`` if its analysis failed (both are best-effort).

    *source_map* is ingestion's ``{path: raw bytes}`` for this rebuild; the
    dead-code prepasses read it instead of re-reading the repo from disk.

    *stored_git_meta* is the persisted per-file git metadata. ``git_meta_map`` holds this run's freshly
    indexed rows, which on an incremental update means the changed files and
    nothing else; every other file would otherwise be scored against an empty
    dict and land on the ``commit_count_90d == 0`` rung of the confidence
    ladder at 0.7 / ``safe_to_delete=True``. It is supplied whole to dead-code
    and to performance ranking, but deliberately not merged into health's
    general ``git_meta_map``: widening that aggregate would silently move
    nonperformance scores (the same reason idle-decay rows stay out of it).

    *stored_performance_callers* is the old-side caller set recovered in one
    store read. It keeps deletion and rename updates authoritative when the
    rebuilt graph can no longer contain the removed execution edge.

    *repo_function_mod_p80* is the repo-wide 80th percentile of per-function
    modification counts, loaded from the persisted ``git_function_blame``
    rollup (see :func:`load_stored_function_mod_p80`). It is passed through to
    the health analyzer so the Function Hotspot gate is scored against the full
    repo instead of this run's changed-files subset (issue #1484). ``None``
    makes the analyzer fall back to deriving it from the walked files.

    ``None`` means the store could not be read, which is different from an
    empty mapping and narrows what the resulting report is allowed to
    overwrite. See ``load_stored_git_meta``.

    ``timings`` records the two analyses as ``analysis.health`` and
    ``analysis.dead_code``. Their costs move independently with the edit, so
    they are never folded into one row.

    ``dead_code=False`` skips the dead-code pass and returns ``None`` for it,
    which keeps the stored findings.
    """
    log = log or _noop_log

    # Run partial code-health analysis up front so both the index-only
    # and full paths can upsert findings/metrics for changed files only.
    # The full file-list is needed because duplication is cross-file —
    # but only files in ``changed_paths`` produce new findings/metrics.
    partial_health_report = None
    if timings is not None:
        timings.start("analysis.health")
    try:
        # Performance is interprocedural. Recompute one bounded bidirectional
        # execution closure so a changed caller can still see an unchanged sink
        # and an unchanged caller can react to a changed sink. The index is
        # built once; this is a multi-source walk, not one walk per finding.
        from repowise.core.analysis.communities import file_community_labels
        from repowise.core.analysis.execution_graph import ExecutionGraphIndex
        from repowise.core.analysis.health import HealthAnalyzer
        from repowise.core.analysis.health.config import HealthConfig
        from repowise.core.analysis.health.scope import scores_language

        _health_changed = {
            fd.path for fd in file_diffs if fd.status in ("added", "modified", "renamed")
        }
        parsed_paths = {pf.file_info.path for pf in parsed_files}
        execution_index = ExecutionGraphIndex(graph_builder.graph())
        _performance_changed = execution_index.affected_files(_health_changed) & parsed_paths
        if stored_performance_callers is not None:
            historical_callers = stored_performance_callers & parsed_paths
            _performance_changed |= (
                execution_index.affected_files(historical_callers) & parsed_paths
            )
        _health_scope = _health_changed | _performance_changed
        _health_analyzer = HealthAnalyzer(
            graph_builder.graph(),
            git_meta_map=git_meta_map,
            parsed_files=parsed_files,
            community_label_map=file_community_labels(graph_builder),
            duplication_cache_dir=Path(repo_path) / ".repowise",
            repo_root=repo_path,
            coverage_map=coverage_map,
        )
        if _health_scope:
            _hcfg = HealthConfig.load(repo_path)
            _analyzer_config = (
                _hcfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])
                if _hcfg.has_overrides()
                else None
            )
            _analyzer_config = _performance_only_config(
                _analyzer_config, _performance_changed - _health_changed
            )
            partial_health_report = _health_analyzer.analyze(
                _analyzer_config,
                changed_files=_health_scope,
                repo_function_mod_p80=repo_function_mod_p80,
                timings=timings,
                # Clone pairs are read only for code; a prose-only change splices nothing.
                duplication_files={
                    pf.file_info.path
                    for pf in parsed_files
                    if pf.file_info.path in _health_changed
                    and scores_language(pf.file_info.language)
                },
            )
            # The closure exists only to refresh interprocedural performance.
            # Preserve the historical changed-file scope for every other
            # dimension, metric, blame row, and refactoring detector.
            partial_health_report.authoritative_paths = set(_health_changed)
            partial_health_report.performance_authoritative_paths = set(_performance_changed)
            partial_health_report.metrics = [
                metric
                for metric in partial_health_report.metrics
                if metric.file_path in _health_changed
            ]
            partial_health_report.findings = [
                finding
                for finding in partial_health_report.findings
                if finding.file_path in _health_changed or finding.dimension == "performance"
            ]
            partial_health_report.refactoring_suggestions = [
                suggestion
                for suggestion in partial_health_report.refactoring_suggestions
                if suggestion.file_path in _health_changed
                or suggestion.refactoring_type == "performance_fix"
            ]
            partial_health_report.function_blame_rows = [
                row
                for row in partial_health_report.function_blame_rows
                if row.get("file_path") in _health_changed
            ]
            log(
                f"Health analysis (partial): [cyan]{len(_health_changed)} changed[/cyan], "
                f"[cyan]{len(_performance_changed)} performance-affected[/cyan], "
                f"[yellow]{len(partial_health_report.findings)} findings[/yellow]"
            )
    except Exception as exc:
        log(f"[yellow]Health analysis skipped: {exc}[/yellow]")
    finally:
        if timings is not None:
            timings.stop("analysis.health")

    # Run dead-code analysis up front so both branches can persist its
    # results. Previously this sat below the ``if index_only``
    # short-circuit, which left the closure's reference to
    # ``dead_code_report`` unbound and crashed every ``--index-only`` run.
    dead_code_report = None
    if not dead_code:
        return partial_health_report, None
    if timings is not None:
        timings.start("analysis.dead_code")
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        # parsed_files enables the source-scan rescues (dynamic markers,
        # bundler aliases, export aliases) on the update path, matching init.
        #
        # This run's freshly indexed rows win over the stored ones for the
        # files they cover; the stored rows carry every other file, which is
        # what init's analyzer had and this path did not.
        _dead_code_git_meta = {**(stored_git_meta or {}), **git_meta_map}
        # Files the walk dropped (size, no language spec, generated). This
        # report is persisted repo-wide (see below), so without them an update
        # would re-derive the verdicts init had clamped and write them back
        # unclamped — the #1237 cascade would return, looking like the fix had
        # regressed.
        _traversal_stats = getattr(graph_builder, "traversal_stats", None)
        _dead_code_analyzer = DeadCodeAnalyzer(
            graph_builder.graph(),
            _dead_code_git_meta,
            parsed_files=graph_builder._parsed_files,
            source_map=source_map,
            repo_root=repo_path,
            unindexed_source_files=(
                _traversal_stats.unindexed_source_files() if _traversal_stats is not None else []
            ),
            dotnet_index=getattr(graph_builder, "dotnet_index", None),
        )
        # Repo-wide, and persisted repo-wide. The detectors were always
        # repo-wide — the update path just discarded everything outside the
        # change set before writing, which is why an unchanged file that the
        # change had made dead (or brought back to life) kept its old verdict
        # until someone re-indexed from scratch.
        dead_code_report = _dead_code_analyzer.analyze()

        # What this report is allowed to overwrite.
        #
        # When the stored read succeeded, this run's git knowledge is the same
        # knowledge a full index had — the stored rows ARE what the last full
        # index wrote, with this run's fresher ones on top — so every file is
        # scored at least as well here as it was there and the report speaks
        # for the whole repository. That includes files with no git row at
        # all: a full index had no row for them either and scored them the
        # same way, so holding them back would protect nothing while leaving
        # exactly the stale verdicts this is meant to fix. Coverage is not the
        # test, and cannot be: ``git_metadata`` legitimately covers only the
        # files ``index_repo`` produced.
        #
        # When the read FAILED, this run knows strictly less than the index it
        # would be overwriting: every file outside the change set would be
        # scored with no git row, which the analyzer holds below the
        # deletion-ready threshold, so a verdict a full index scored at 1.0
        # would be written back as an unscored 0.5. So it speaks only for the
        # files it re-indexed this run, which is the behavior this path had
        # before it was widened.
        dead_code_report.authoritative_paths = (
            None if stored_git_meta is not None else frozenset(git_meta_map)
        )
        if dead_code_report.authoritative_paths is not None:
            log(
                "[yellow]Dead-code findings limited to "
                f"{len(dead_code_report.authoritative_paths)} re-indexed files; "
                "the rest keep their previous verdict[/yellow]"
            )
        if dead_code_report.total_findings:
            log(
                f"Dead code findings: [yellow]{dead_code_report.total_findings}[/yellow]"
                " repo-wide, every kind"
            )
    except Exception as exc:
        log(f"[yellow]Dead code analysis skipped: {exc}[/yellow]")
    finally:
        if timings is not None:
            timings.stop("analysis.dead_code")

    return partial_health_report, dead_code_report


@dataclass(frozen=True)
class DocDriftUpdate:
    """What an update tells the drift pass about the change it is applying."""

    base_ref: str | None = None
    """The commit the update diffs from, to the working tree."""
    changed_paths: tuple[str, ...] = ()
    """Every path the update adds, edits, deletes or renames (both sides)."""
    symbol_names: frozenset[str] | None = None
    """The index's symbol names; ``None`` reads them from the graph."""

    @classmethod
    def from_file_diffs(cls, base_ref: str | None, file_diffs: Iterable[Any]) -> DocDriftUpdate:
        paths = tuple(p for fd in file_diffs for p in (fd.path, fd.old_path) if p)
        return cls(base_ref=base_ref, changed_paths=paths)


def run_doc_drift_partial(
    graph_builder: Any,
    source_map: dict[str, bytes] | None,
    *,
    repo_path: Any | None = None,
    log: LogFn | None = None,
    timings: PhaseTimings | None = None,
    update: DocDriftUpdate | None = None,
) -> Any | None:
    """Re-check the repository's own markdown on the incremental path.

    Its own function rather than a third member of
    :func:`run_partial_analysis`'s tuple, mirroring the full path where drift is
    its own phase. Returns ``None`` when the pass could not run, which the
    caller must treat as "write nothing".

    The cheap kinds are re-derived for every document. Symbol references are
    re-resolved only where *update* could have changed them (see
    :class:`~repowise.core.analysis.doc_drift.symbols.SymbolRecheck`); the rest
    carry forward in the store.
    """
    log = log or _noop_log
    if not source_map:
        return None

    with timed(timings, "analysis.doc_drift"):
        try:
            from repowise.core.analysis.doc_drift import DocDriftAnalyzer

            # The traversal, not the parse set. ``source_map`` omits files that
            # failed to read or parse, and resolving against that narrower tree
            # turns a live path into a fabricated "missing" finding.
            tracked_paths = getattr(graph_builder, "traversed_file_paths", None)
            if not tracked_paths:
                return None

            root = Path(repo_path) if repo_path else None
            report = DocDriftAnalyzer(
                source_map=source_map,
                tracked_paths=tracked_paths,
                repo_root=root,
                symbols=_drift_symbol_options(graph_builder, root, update or DocDriftUpdate()),
            ).analyze()
            report.authoritative_paths = report.documents
            if report.total_findings:
                log(f"Doc drift findings: [yellow]{report.total_findings}[/yellow]")
            return report
        except Exception as exc:
            log(f"[yellow]Doc drift analysis skipped: {exc}[/yellow]")
            return None


def _drift_symbol_options(graph_builder: Any, root: Path | None, update: DocDriftUpdate) -> Any:
    """The drift pass's symbol options for *update*; ``None`` without a working tree."""
    from repowise.core.analysis.doc_drift.symbols import (
        SymbolOptions,
        graph_symbol_names,
        symbol_recheck,
    )

    if root is None:
        return None
    names = update.symbol_names
    if names is None:
        names = graph_symbol_names(graph_builder.graph())
    return SymbolOptions(names, symbol_recheck(root, update.base_ref, update.changed_paths))

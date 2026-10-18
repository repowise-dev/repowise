"""Partial health persistence and the history refresh of unchanged files."""

from __future__ import annotations

from typing import Any

from ._common import logger


async def refresh_unchanged_history(
    session: Any, repo_id: str, repo_path: Any, *, skip_paths: set[str]
) -> int:
    """Re-score the git-derived markers on files this run did not walk.

    The update refreshes git metadata for the whole repository, so a file the
    change set never touched can still have moved in every way history
    measures — new co-change partners, a fix that landed elsewhere in the same
    commit, an owner who left. Only those markers are re-evaluated; each file
    keeps the structural findings its last parse produced, so nothing here
    needs the source.

    Returns the number of files rewritten. Best-effort: the update is not worth
    failing over a refresh.
    """
    from repowise.core.analysis.health.config import HealthConfig
    from repowise.core.analysis.health.history_refresh import (
        REFRESHABLE_MARKERS,
        active_contributors,
        git_meta_rows_to_map,
        refresh_history,
    )
    from repowise.core.persistence.crud import (
        get_all_git_metadata,
        get_file_language_map,
        get_health_findings,
        get_health_metrics,
        upsert_health_findings,
        upsert_health_metrics,
    )
    from repowise.core.pipeline.persist import _analyzed_commit

    stored = {
        m.file_path: m
        for m in await get_health_metrics(session, repo_id)
        if m.file_path not in skip_paths
    }
    if not stored:
        return 0
    git_meta = git_meta_rows_to_map((await get_all_git_metadata(session, repo_id)).values())
    if not git_meta:
        return 0

    findings_by_path: dict[str, list[Any]] = {}
    for finding in await get_health_findings(session, repo_id, include_withheld=True):
        findings_by_path.setdefault(finding.file_path, []).append(finding)

    cfg = HealthConfig.load(repo_path)
    refreshed = refresh_history(
        metrics=list(stored.values()),
        findings_by_path=findings_by_path,
        git_meta_by_path=git_meta,
        languages=await get_file_language_map(session, repo_id),
        repo_active_contributors_90d=active_contributors(git_meta),
        severity_overrides=cfg.severity_overrides or None,
    )
    if not refreshed:
        return 0

    # The two writes are scoped separately on purpose. A metric row is cheap to
    # rewrite and carries no identity, but rewriting findings deletes and
    # re-inserts them, which means new ids and a reset ``created_at`` — so that
    # only happens for a file whose history findings actually changed, not for
    # every file whose stored split was merely never recorded.
    commit = await _analyzed_commit(session, repo_id)
    metric_writes = [
        _refreshed_metric(r, stored[r.file_path], commit)
        for r in refreshed
        if _numbers_moved(r, stored[r.file_path])
    ]
    finding_writes = [
        r for r in refreshed if _history_findings_moved(r, findings_by_path.get(r.file_path, []))
    ]
    if not metric_writes and not finding_writes:
        return 0

    # One savepoint over both: a file whose findings were rewritten but whose
    # score was not would report a deduction its own findings cannot account
    # for, and the caller logs this step and carries on to commit the rest.
    async with session.begin_nested():
        if finding_writes:
            await upsert_health_findings(
                session,
                repo_id,
                [f for r in finding_writes for f in r.findings],
                file_paths=[r.file_path for r in finding_writes],
            )
        if metric_writes:
            await upsert_health_metrics(session, repo_id, metric_writes)
    logger.debug(
        "health_history_refreshed",
        repo_id=repo_id,
        metric_rows=len(metric_writes),
        finding_rows=len(finding_writes),
        markers=len(REFRESHABLE_MARKERS),
    )
    return len(finding_writes)


def _numbers_moved(refreshed: Any, stored: Any) -> bool:
    """Whether the stored row's numbers differ from the re-scored ones.

    Includes a row whose split was never recorded, so the first update after
    the split ships backfills it without touching any finding.
    """
    return (
        _differs(refreshed.score, stored.score, 2)
        or _differs(refreshed.structure_deduction, stored.structure_deduction, 3)
        or _differs(refreshed.history_deduction, stored.history_deduction, 3)
    )


def _differs(new: float | None, old: float | None, places: int) -> bool:
    """``None`` is a value here: a file that is no longer scored has moved."""
    if new is None or old is None:
        return (new is None) != (old is None)
    return round(new, places) != round(float(old), places)


def _history_findings_moved(refreshed: Any, stored_findings: list[Any]) -> bool:
    """Whether the git-derived findings themselves changed.

    Compared on what a reader sees — which markers fired, where, and how
    severely — so a file keeps its finding ids and their ages across an update
    that re-derived the same history.
    """

    def key(findings: Any) -> set:
        return {
            (
                getattr(f, "biomarker_type", ""),
                str(getattr(f, "severity", "")),
                getattr(f, "function_name", None),
                round(float(getattr(f, "health_impact", 0.0) or 0.0), 3),
            )
            for f in findings
        }

    return key(refreshed.findings) != key(stored_findings)


def _refreshed_metric(refreshed: Any, stored: Any, commit: str | None) -> dict:
    """The stored metric row with the rescored numbers written over it.

    A dict rather than a dataclass so the fields this pass cannot know — the
    complexity and coverage columns, which need a parse — keep their stored
    values instead of being reset to a default.

    ``commit`` is the one the row was rescored against: its history is read at
    it, and the file is unchanged since its last parse. Keeping the old stamp
    made the newest row name a commit 100 behind, which the health basis
    reads. Set here, not as the writer's default, so an unreadable HEAD
    clears the stamp: "not recorded" is honest and the old commit is not.
    """
    # A file health never walked carries no complexity figures either, which
    # also clears the ones a store written before that rule kept.
    walked = refreshed.score is not None
    return {
        "file_path": refreshed.file_path,
        "analyzed_commit": commit,
        "score": refreshed.score,
        "max_ccn": stored.max_ccn if walked else None,
        "max_nesting": stored.max_nesting if walked else None,
        "nloc": stored.nloc,
        "duplication_pct": stored.duplication_pct,
        "has_test_file": stored.has_test_file,
        "line_coverage_pct": stored.line_coverage_pct,
        "branch_coverage_pct": stored.branch_coverage_pct,
        "module": stored.module,
        "defect_score": refreshed.defect_score,
        "maintainability_score": refreshed.maintainability_score,
        "performance_score": refreshed.performance_score,
        "structure_deduction": refreshed.structure_deduction,
        "history_deduction": refreshed.history_deduction,
        "is_test": stored.is_test,
        "code_origin": getattr(stored, "code_origin", None),
    }


async def persist_partial_health(
    session: Any, repo_id: str, report: Any, repo_path: Any = None
) -> None:
    """Upsert health findings + metrics for the changed-files subset.

    Unlike ``persist_pipeline_result`` (which delete-then-inserts the
    whole repo), this writer only touches rows whose ``file_path`` is in
    the partial report — so unchanged files keep their existing findings
    and metrics across an incremental ``repowise update``.
    """
    from repowise.core.persistence.crud import (
        PayoffContext,
        finalize_performance_opportunities,
        finalize_refactoring_opportunities,
        upsert_health_findings,
        upsert_health_metrics,
        upsert_refactoring_suggestions,
        write_function_facts,
    )
    from repowise.core.pipeline.persist import _analyzed_commit, path_liveness

    changed_paths = sorted(
        set(getattr(report, "authoritative_paths", None) or ())
        or {m.file_path for m in report.metrics or []}
    )
    performance_paths = sorted(
        set(getattr(report, "performance_authoritative_paths", None) or ()) - set(changed_paths)
    )
    # Nothing analysed: a deletion-only update runs no health analysis, so it
    # has no role map to restamp stored roles with. Roles, like performance
    # findings, refresh on the next run that analyses any file.
    if not changed_paths and not performance_paths:
        return
    analyzed_commit = await _analyzed_commit(session, repo_id)
    # One savepoint over the findings and everything derived from them. This
    # writer's caller logs a failed step and carries on to commit the rest of
    # the run, so without the savepoint a rebuild that failed halfway would
    # leave the queue describing findings that were never stored.
    async with session.begin_nested():
        if changed_paths:
            await upsert_health_metrics(
                session,
                repo_id,
                report.metrics or [],
                analyzed_commit=analyzed_commit,
            )
            await upsert_health_findings(
                session, repo_id, list(report.findings or []), file_paths=changed_paths
            )
        if performance_paths:
            await upsert_health_findings(
                session,
                repo_id,
                list(report.findings or []),
                file_paths=performance_paths,
                dimension="performance",
            )
        # Refactoring suggestions for the changed files only (unchanged files
        # keep theirs). Scoped reconciliation across the full changed-file set,
        # so a file that became clean has its plans resolved rather than left
        # standing, and a plan that survived the edit keeps its id.
        if changed_paths:
            await upsert_refactoring_suggestions(
                session,
                repo_id,
                [
                    suggestion
                    for suggestion in (getattr(report, "refactoring_suggestions", None) or [])
                    if suggestion.refactoring_type != "performance_fix"
                ],
                file_paths=changed_paths,
                # Before the facts below replace the stored ones: those are the
                # measures a resolved plan was detected against.
                payoff=PayoffContext(
                    fact_rows=getattr(report, "function_facts", None),
                    is_live=path_liveness(repo_path),
                    commit=analyzed_commit,
                ),
            )
        # Facts for the files this run walked; roles and nearest tests for every
        # stored row, written only where they moved. No rows means no graph to
        # key them on: the stored ones stay. Before the finalizers, which read
        # each plan's nearest tests off these rows.
        fact_rows = getattr(report, "function_facts", None)
        if fact_rows is not None:
            await write_function_facts(
                session,
                repo_id,
                fact_rows,
                file_paths={*changed_paths, *performance_paths},
                roles=getattr(report, "execution_roles", None),
                tests=getattr(report, "function_tests", None),
            )
        # A partial run sees a subset of the findings, so the plans and the
        # queue it would derive are a subset too. Both are rebuilt here instead,
        # from the merged stored rows, by the same writer the full path uses.
        # That is what makes the two paths agree, and it retires the file-scoped
        # plan bookkeeping that used to try to reach a shared intervention from
        # its callers.
        await finalize_performance_opportunities(
            session,
            repo_id,
            analyzed_commit=analyzed_commit,
            plan_policy=getattr(report, "performance_plan_policy", None),
            execution_roles=getattr(report, "execution_roles", None),
        )
        # Repository-wide, like the queue above and for the same reason: an
        # opportunity folds a file's plans, and a file this run did not touch
        # can still lose one when a cross-file plan elsewhere resolves.
        await finalize_refactoring_opportunities(session, repo_id, analyzed_commit=analyzed_commit)
    # Per-function blame rollup for the changed files (keeps git_function_blame
    # current between full indexes; FULL git tier only — empty otherwise).
    fn_blame_rows = getattr(report, "function_blame_rows", None)
    if fn_blame_rows:
        from repowise.core.persistence.crud import upsert_git_function_blame_bulk

        await upsert_git_function_blame_bulk(session, repo_id, fn_blame_rows)
    # Only a full index rewrites the whole population, so rows an older version
    # scored for prose or configuration go here rather than lingering in the
    # average, and rows written before ``is_test`` existed get it from their
    # path. Both before the refresh, which should not re-score a row that is
    # about to be deleted.
    from repowise.core.persistence.crud import (
        backfill_is_test,
        clear_unanalysed_scores,
        prune_unscored_health_rows,
    )

    await prune_unscored_health_rows(session, repo_id)
    await clear_unanalysed_scores(session, repo_id)
    await backfill_is_test(session, repo_id)
    # Then the files this run did not walk, whose git-derived markers the fresh
    # metadata may have moved. Before the snapshot, or the trend would describe
    # the store as it stood one update ago.
    if repo_path is not None:
        try:
            await refresh_unchanged_history(
                session, repo_id, repo_path, skip_paths=set(changed_paths)
            )
        except Exception as exc:
            # Never fails the run: the next update re-derives the whole thing
            # from the stored git metadata, so nothing is stranded behind.
            logger.debug("health_history_refresh_skipped", repo_id=repo_id, error=str(exc))
    # The store now holds the merged repository, so the snapshot describes the
    # whole of it, not this run's changed files.
    from repowise.core.pipeline.persist import snapshot_health_from_store

    await snapshot_health_from_store(session, repo_id)

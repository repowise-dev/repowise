"""Per-file health metrics: the writers, the in-place repairs an update runs
over stored rows, and the worst-first read."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from .....analysis.health.perf.coverage import PerfCoverage

from .....analysis.health.complexity.languages import has_health_dialect

# The comparator is pure and lives with the health read models; only the SQL
# that feeds it belongs here. Re-imported rather than moved out of reach: the
# crud surface is where the MCP tool and the tests already read it from.
from .....analysis.health.ranking import sort_metrics_worst_first
from .....analysis.health.scope import scores_language
from .....analysis.health.scoring import SCORE_FIELDS
from .....test_paths import is_test_related_path
from ....models import GraphNode, HealthFileMetric, HealthFinding, _new_uuid
from ..._shared import _BATCH_SIZE, _batch_delete_in
from .exclude import _filter_excluded_paths, health_exclude_spec
from .findings import get_deduction_by_path


def _opt(cast: Any, value: Any) -> Any:
    """*value* through *cast*, keeping ``None`` (a file health never walked)."""
    return None if value is None else cast(value)


def _health_metric_row_data(metric: Any) -> dict:
    """One metric dataclass as column values. Both writers go through here."""
    return {
        "file_path": metric.file_path,
        "score": _opt(float, metric.score),
        "max_ccn": _opt(int, metric.max_ccn),
        "max_nesting": _opt(int, metric.max_nesting),
        "nloc": int(metric.nloc),
        "duplication_pct": metric.duplication_pct,
        "has_test_file": bool(metric.has_test_file),
        "line_coverage_pct": metric.line_coverage_pct,
        "branch_coverage_pct": metric.branch_coverage_pct,
        "module": metric.module,
        "defect_score": getattr(metric, "defect_score", None),
        "maintainability_score": getattr(metric, "maintainability_score", None),
        "performance_score": getattr(metric, "performance_score", None),
        "structure_deduction": getattr(metric, "structure_deduction", None),
        "history_deduction": getattr(metric, "history_deduction", None),
        "is_test": bool(getattr(metric, "is_test", False)),
        "code_origin": getattr(metric, "code_origin", None),
    }


async def save_health_metrics(
    session: AsyncSession,
    repository_id: str,
    metrics: list[Any],
    *,
    analyzed_commit: str | None = None,
) -> None:
    """Replace per-file health metrics for *repository_id*.

    Delete-then-insert (matches the findings writer). The unique
    constraint on (repository_id, file_path) means we cannot leave
    stale rows around without an upsert dance — delete-and-insert keeps
    it simple and aligns with how dead-code findings are written.

    ``analyzed_commit`` stamps every written row with the commit these scores
    were computed against — the same pattern as ``save_coverage_files``'
    ``ingested_commit_sha``. Health runs as its own pass and can lag the index,
    so ``Repository.head_commit`` cannot answer "how old is this score". Omitted
    → NULL, which reads as "not recorded" rather than "current".
    """
    await session.execute(
        delete(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
    )

    for i in range(0, len(metrics), _BATCH_SIZE):
        batch = metrics[i : i + _BATCH_SIZE]
        for m in batch:
            data = _health_metric_row_data(m) if hasattr(m, "file_path") else dict(m)
            # After the dict-passthrough branch so an explicit per-row value in a
            # raw dict still wins; the sha is a property of the pass, not the row.
            if analyzed_commit is not None:
                data.setdefault("analyzed_commit", analyzed_commit)

            session.add(
                HealthFileMetric(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(HealthFileMetric, k)
                    },
                )
            )
        await session.flush()


async def upsert_health_metrics(
    session: AsyncSession,
    repository_id: str,
    metrics: list[Any],
    *,
    analyzed_commit: str | None = None,
) -> None:
    """Upsert per-file metrics; unchanged files in the table stay put.

    Sibling of ``save_health_metrics`` (which delete-then-inserts the
    whole repo). Used by the incremental analysis path so a partial
    re-index never wipes metric rows for files that weren't touched.

    ``analyzed_commit`` stamps only the rows this call rewrites, which is the
    point of recording it per row: after a partial pass the table honestly
    reports two commits, and ``get_health``'s ``_meta`` says so rather than
    claiming one scoring commit for the whole repo.
    """
    if not metrics:
        return
    paths = [m.file_path if hasattr(m, "file_path") else m["file_path"] for m in metrics]
    existing = await session.execute(
        select(HealthFileMetric).where(
            HealthFileMetric.repository_id == repository_id,
            HealthFileMetric.file_path.in_(paths),
        )
    )
    by_path = {row.file_path: row for row in existing.scalars().all()}

    for m in metrics:
        data = _health_metric_row_data(m) if hasattr(m, "file_path") else dict(m)
        # Only when the caller knows the sha. An unconditional set would push a
        # None over a sha already on the row, so a caller that simply does not
        # track the commit would erase the stamp a caller that does had written.
        if analyzed_commit is not None:
            data.setdefault("analyzed_commit", analyzed_commit)

        row = by_path.get(data["file_path"])
        if row is not None:
            for k, v in data.items():
                if k in ("id", "repository_id") or not hasattr(HealthFileMetric, k):
                    continue
                setattr(row, k, v)
        else:
            session.add(
                HealthFileMetric(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(HealthFileMetric, k)
                    },
                )
            )
    await session.flush()


async def get_scored_file_paths(session: AsyncSession, repository_id: str) -> set[str]:
    """The files health currently scores, read off the stored metric rows."""
    q = select(HealthFileMetric.file_path).where(
        HealthFileMetric.repository_id == repository_id
    )
    return {path for (path,) in (await session.execute(q)).all()}


async def backfill_is_test(session: AsyncSession, repository_id: str) -> int:
    """Stamp ``is_test`` on rows written before the column existed.

    The answer comes from the path, not from a score, so an existing store gets
    it on the next update instead of waiting for a re-index. Without this a
    ``scope=production`` request on an un-rescored store would quietly answer
    with the whole repository. Returns the number of rows filled.
    """
    rows = (
        await session.execute(
            select(HealthFileMetric).where(
                HealthFileMetric.repository_id == repository_id,
                HealthFileMetric.is_test.is_(None),
            )
        )
    ).scalars().all()
    if not rows:
        return 0
    languages = await get_file_language_map(session, repository_id)
    for row in rows:
        row.is_test = is_test_related_path(row.file_path, languages.get(row.file_path))
    await session.flush()
    return len(rows)


async def prune_unscored_health_rows(session: AsyncSession, repository_id: str) -> int:
    """Drop metrics and findings for files health no longer scores.

    A full index rewrites the whole population and needs nothing here, but an
    update only touches the files that changed, so prose and configuration rows
    written by an older version would otherwise sit in the average forever.
    Files with no graph node are left alone: no language is not the same answer
    as a language health does not score. Returns the number of rows removed,
    which is zero on every update after the first.
    """
    languages = await get_file_language_map(session, repository_id)
    stale = [path for path, language in languages.items() if not scores_language(language)]
    if not stale:
        return 0
    removed = 0
    for model in (HealthFileMetric, HealthFinding):
        removed += await _batch_delete_in(
            session, model, model.file_path, stale, prefilter=(model.repository_id == repository_id,)
        )
    return removed


async def clear_unanalysed_scores(session: AsyncSession, repository_id: str) -> int:
    """Drop the numbers stored for files in a language health has no dialect for.

    A store written before such files were left unscored holds a 10.0 and a
    max_ccn of 1 for each, which every average would keep counting until a full
    rescore. The answer comes from the language alone, so an update fixes it in
    place, git history or not. Files with no graph node are left alone, as in
    ``prune_unscored_health_rows``. Returns the number of rows cleared.
    """
    languages = await get_file_language_map(session, repository_id)
    paths = [p for p, lang in languages.items() if lang and not has_health_dialect(lang)]
    cleared = 0
    for i in range(0, len(paths), _BATCH_SIZE):
        rows = await session.execute(
            select(HealthFileMetric).where(
                HealthFileMetric.repository_id == repository_id,
                HealthFileMetric.file_path.in_(paths[i : i + _BATCH_SIZE]),
                HealthFileMetric.score.is_not(None),
            )
        )
        for row in rows.scalars().all():
            for name in (*SCORE_FIELDS, "max_ccn", "max_nesting"):
                setattr(row, name, None)
            cleared += 1
    await session.flush()
    return cleared


async def backfill_module_attribution(
    session: AsyncSession,
    repository_id: str,
    package_roots: set[str],
) -> int:
    """Recompute every metric row's ``module`` from the repo layout.

    Returns the number of rows changed, so a caller can stay silent when there
    was nothing to correct.

    ``module`` is a pure function of ``(file_path, package_roots)`` — no parse,
    no symbols, no git, no model — so correcting it never needs the rows
    rewritten by a re-score, let alone a re-index. That is the whole point:
    a directory label must not cost users an indexing run.

    Idempotent by construction, and it converges with the indexer rather than
    fighting it: both call the same :func:`module_for` over the same
    disk-scanned roots, so a full index writing a *different* value would mean
    the two disagree about the repo layout, and running this again would flip
    it back rather than settle.
    """
    from .....ingestion.package_roots import module_for

    result = await session.execute(
        select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
    )
    changed = 0
    for row in result.scalars().all():
        expected = module_for(row.file_path, package_roots)
        if row.module != expected:
            row.module = expected
            changed += 1
    if changed:
        await session.flush()
    return changed


async def count_health_files(session: AsyncSession, repository_id: str) -> int:
    """Code files health read, scored or not, inside the health exclude rules.

    The same population :func:`get_health_metrics` returns, counted without
    loading the rows.
    """
    result = await session.execute(
        select(HealthFileMetric.file_path).where(HealthFileMetric.repository_id == repository_id)
    )
    rows = result.all()
    return len(_filter_excluded_paths(rows, await health_exclude_spec(session, repository_id)))


async def get_health_metrics(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
) -> list[HealthFileMetric]:
    """Per-file health metrics, ordered worst-first.

    See :func:`sort_metrics_worst_first` for why the order is not a plain
    ``ORDER BY score``.
    """
    q = select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
    if file_paths is not None:
        q = q.where(HealthFileMetric.file_path.in_(file_paths))
    result = await session.execute(q)
    rows = _filter_excluded_paths(
        list(result.scalars().all()),
        await health_exclude_spec(session, repository_id),
    )
    if len(rows) < 2:
        # Nothing to order. Skips the aggregate for the single-file lookups
        # (symbol drawer, file detail, one-plan refactoring view), which are
        # per-request reads that would otherwise pay for a ranking they cannot
        # use.
        return rows
    # Scoped to the caller's paths, not to ``rows``: a repo-wide read wants the
    # single grouped scan, and the extra keys an exclude config leaves in the
    # map are never looked up.
    return sort_metrics_worst_first(
        rows,
        await get_deduction_by_path(session, repository_id, file_paths=file_paths),
    )


async def get_file_language_map(session: AsyncSession, repository_id: str) -> dict[str, str]:
    """``{file_path: language_tag}`` for every file node in the graph."""
    q = select(GraphNode.node_id, GraphNode.language).where(
        GraphNode.repository_id == repository_id,
        GraphNode.node_type == "file",
    )
    return {node_id: language for node_id, language in (await session.execute(q)).all()}


async def get_perf_coverage(session: AsyncSession, repository_id: str) -> PerfCoverage:
    """How much of the analyzed code the performance pass was able to run on."""
    # Imported lazily to keep the persistence layer free of an analysis-layer
    # import at module load (and to avoid a circular import).
    from .....analysis.health.perf.coverage import coverage_for_metrics

    metrics = await get_health_metrics(session, repository_id)
    lang_by_path = await get_file_language_map(session, repository_id)
    return coverage_for_metrics(metrics, lang_by_path)

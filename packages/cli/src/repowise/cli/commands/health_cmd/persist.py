"""Persist the analyzer's health output to the repo's wiki.db.

Coverage is *read* here (to fold into scoring) but never written: the
``coverage`` command group owns ingestion of both per-file coverage and the
per-test map. A ``repowise health`` run must not overwrite that data.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from repowise.cli.helpers import console, run_async


def _load_persisted_coverage_map(repo_path: object) -> dict[str, dict]:
    """Build the analyzer ``coverage_map`` from persisted per-file coverage.

    Reads whatever ``repowise coverage add`` (or index-time ingest) stored in
    ``coverage_files`` so ``repowise health`` reflects it without a flag.
    Best-effort: no repo row / no rows / any DB error yields an empty map.
    """
    from repowise.cli.helpers import get_db_url_for_repo, reconcile_schema_best_effort
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        load_coverage_map,
    )

    async def _do() -> dict[str, dict]:
        url = get_db_url_for_repo(repo_path)
        # Without this the ORM's `no such column` on a store one repowise
        # older is swallowed below and reads as "no coverage".
        await reconcile_schema_best_effort(url)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return {}
            return await load_coverage_map(session, repo.id)

    try:
        return run_async(_do())
    except Exception:
        return {}


def _load_fix_first(repo_path: object) -> Any:
    """The whole stored Fix-first queue, or ``None`` when the store cannot answer.

    Best-effort like the coverage read: a missing repo row or an older store
    leaves the report without the section; the report itself still prints.
    """
    from repowise.cli.helpers import repo_index_session
    from repowise.core.persistence.crud.analysis.fix_first import load_fix_first

    async def _do() -> Any:
        async with repo_index_session(Path(str(repo_path))) as opened:
            if opened is None:
                return None
            session, repo_id = opened
            return await load_fix_first(session, repo_id, limit=None)

    try:
        return run_async(_do())
    except Exception:
        return None


def _load_queue_counts(repo_path: object) -> dict[str, Any] | None:
    """Every queue unit's stored counts, by noun; ``None`` when the store cannot answer."""
    from repowise.cli.helpers import repo_index_session
    from repowise.core.persistence.crud.analysis.queue_counts import all_unit_counts

    async def _do() -> Any:
        async with repo_index_session(Path(str(repo_path))) as opened:
            if opened is None:
                return None
            session, repo_id = opened
            return await all_unit_counts(session, repo_id)

    try:
        return run_async(_do())
    except Exception:
        return None


def _load_stored_report(repo_path: object) -> tuple[Any, set[str], dict[str, str]] | None:
    """The health analysis the last ``init``/``update`` stored, read without writing.

    ``(report, hotspot paths, language by path)``, where the report carries the
    stored metrics and open findings and the KPIs computed over them as the
    indexer computes its snapshot. ``None`` when no store or no analysis opens;
    a store an older repowise wrote raises ``StaleIndexError`` from the session.

    Not ``crud.get_health_summary``: that is the API's summary (open-finding
    counts, perf coverage) and carries no hotspot, production or worst-test
    figure, while the report renders ``compute_kpis``'s, the figures the trend
    snapshot stores. The rows are read once and serve both the KPIs and the
    defect-accuracy line.
    """
    import click
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.cli.helpers import repo_index_session
    from repowise.core.analysis.health.scoring import compute_kpis
    from repowise.core.persistence.crud import (
        get_file_language_map,
        get_health_findings,
        get_health_metrics,
        get_hotspot_file_paths,
    )

    async def _do() -> tuple[Any, set[str], dict[str, str]] | None:
        async with repo_index_session(Path(str(repo_path))) as opened:
            if opened is None:
                return None
            session, repo_id = opened
            metrics = await get_health_metrics(session, repo_id)
            if not metrics:
                return None
            findings = await get_health_findings(session, repo_id)
            hotspots = await get_hotspot_file_paths(session, repo_id)
            languages = await get_file_language_map(session, repo_id)
        kpis = compute_kpis(metrics, hotspots)
        return SimpleNamespace(metrics=metrics, findings=findings, kpis=kpis), hotspots, languages

    try:
        return run_async(_do())
    except SQLAlchemyError as exc:
        # Locked or damaged: not the same answer as "nothing stored".
        raise click.ClickException(f"Could not read the stored health analysis: {exc}") from exc


def _load_recommendations(
    repo_path: Path, suggestions: Sequence[Any], metrics: Sequence[Any]
) -> list[dict[str, Any]]:
    """Canonical CLI recommendations, enriched from the local store in bulk."""
    from repowise.cli.helpers import get_db_url_for_repo, reconcile_schema_best_effort
    from repowise.core.analysis.health.refactoring.recommendations import (
        build_recommendations,
        hydrate_recommendations,
        serialize_recommendations,
    )
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path

    async def _do() -> list[dict[str, Any]]:
        url = get_db_url_for_repo(repo_path)
        await reconcile_schema_best_effort(url)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return []
            recommendations = await hydrate_recommendations(
                session, repo.id, suggestions, metric_rows=metrics
            )
            return serialize_recommendations(recommendations)

    try:
        hydrated = run_async(_do())
        if hydrated:
            return hydrated
    except Exception:
        pass
    return serialize_recommendations(
        build_recommendations(
            suggestions,
            metric_by_path={getattr(metric, "file_path", ""): metric for metric in metrics},
        )
    )


def _persist_health(repo_path: object, *, report: object) -> None:
    """Write the analyzer's health output to the repo's wiki.db.

    Mirrors what ``pipeline/persist.py`` does for ``repowise init``:
    overwrite the health tables for this repo with the freshly computed
    values. Coverage tables are left untouched (owned by ``coverage add``).
    Best-effort — a missing repo row or a DB error logs to stderr and
    returns; the CLI does not crash.
    """
    from repowise.cli.helpers import get_db_url_for_repo, reconcile_schema_best_effort
    from repowise.core.analysis.health.trends import snapshot_fields
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        save_health_findings,
        save_health_metrics,
        save_health_snapshot,
    )
    from repowise.core.workspace.update import get_head_commit

    async def _do() -> None:
        url = get_db_url_for_repo(repo_path)
        await reconcile_schema_best_effort(url)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                console.print(
                    "[yellow]No repository row yet — run `repowise init` once "
                    "before persisting health updates.[/yellow]"
                )
                return
            repo_id = repo.id

            await save_health_metrics(
                session,
                repo_id,
                list(getattr(report, "metrics", []) or []),
                analyzed_commit=get_head_commit(repo_path),
            )
            findings = list(getattr(report, "findings", []) or [])
            if findings:
                await save_health_findings(session, repo_id, findings)

            kpis = getattr(report, "kpis", {}) or {}
            metrics = getattr(report, "metrics", []) or []
            try:
                fields = snapshot_fields(kpis, metrics, findings)
                if fields is not None:
                    await save_health_snapshot(session, repo_id, **fields)
            except Exception as exc:
                console.print(f"[yellow]Snapshot write skipped: {exc}[/yellow]")

            await session.commit()

    try:
        run_async(_do())
    except Exception as exc:
        console.print(f"[red]Could not persist health to DB: {exc}[/red]")

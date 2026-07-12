"""Persist the analyzer's health output to the repo's wiki.db.

Coverage is *read* here (to fold into scoring) but never written: the
``coverage`` command group owns ingestion of both per-file coverage and the
per-test map. A ``repowise health`` run must not overwrite that data.
"""

from __future__ import annotations

import json

from repowise.cli.helpers import console, run_async


def _load_persisted_coverage_map(repo_path: object) -> dict[str, dict]:
    """Build the analyzer ``coverage_map`` from persisted per-file coverage.

    Reads whatever ``repowise coverage add`` (or index-time ingest) stored in
    ``coverage_files`` so ``repowise health`` reflects it without a flag.
    Best-effort: no repo row / no rows / any DB error yields an empty map.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        load_coverage_for_repo,
    )

    async def _do() -> dict[str, dict]:
        engine = create_engine(get_db_url_for_repo(repo_path))
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return {}
            rows = await load_coverage_for_repo(session, repo.id)
            out: dict[str, dict] = {}
            for r in rows:
                try:
                    covered = list(json.loads(r.covered_lines_json))
                except (ValueError, TypeError):
                    covered = []
                out[r.file_path] = {
                    "line_coverage_pct": r.line_coverage_pct,
                    "branch_coverage_pct": r.branch_coverage_pct,
                    "covered_lines": covered,
                    "total_coverable_lines": r.total_coverable_lines,
                    "source_format": r.source_format,
                }
            return out

    try:
        return run_async(_do())
    except Exception:
        return {}


def _persist_health(repo_path: object, *, report: object) -> None:
    """Write the analyzer's health output to the repo's wiki.db.

    Mirrors what ``pipeline/persist.py`` does for ``repowise init``:
    overwrite the health tables for this repo with the freshly computed
    values. Coverage tables are left untouched (owned by ``coverage add``).
    Best-effort — a missing repo row or a DB error logs to stderr and
    returns rather than crashing the CLI.
    """
    from repowise.cli.helpers import get_db_url_for_repo
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

    async def _do() -> None:
        url = get_db_url_for_repo(repo_path)
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

            await save_health_metrics(session, repo_id, list(getattr(report, "metrics", []) or []))
            findings = list(getattr(report, "findings", []) or [])
            if findings:
                await save_health_findings(session, repo_id, findings)

            kpis = getattr(report, "kpis", {}) or {}
            metrics = getattr(report, "metrics", []) or []
            try:
                await save_health_snapshot(
                    session,
                    repo_id,
                    hotspot_health=float(kpis.get("hotspot_health", 10.0)),
                    average_health=float(kpis.get("average_health", 10.0)),
                    worst_performer_path=kpis.get("worst_performer_path"),
                    worst_performer_score=kpis.get("worst_performer_score"),
                    per_file_scores={m.file_path: round(float(m.score), 2) for m in metrics},
                )
            except Exception as exc:
                console.print(f"[yellow]Snapshot write skipped: {exc}[/yellow]")

            await session.commit()

    try:
        run_async(_do())
    except Exception as exc:
        console.print(f"[red]Could not persist health to DB: {exc}[/red]")

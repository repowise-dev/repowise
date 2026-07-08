"""Persist the analyzer's health + coverage output to the repo's wiki.db."""

from __future__ import annotations

from repowise.cli.helpers import console, run_async


def _persist_health(
    repo_path: object,
    *,
    report: object,
    coverage_files: list,
    coverage_format: str | None,
) -> None:
    """Write the analyzer's output to the repo's wiki.db.

    Mirrors what ``pipeline/persist.py`` does for ``repowise init``:
    overwrite the four health tables for this repo with the freshly
    computed values. Best-effort — a missing repo row or a DB error
    logs to stderr and returns rather than crashing the CLI.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        save_coverage_files,
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
            head_sha = getattr(repo, "head_commit", None)

            if coverage_files:
                await save_coverage_files(
                    session,
                    repo_id,
                    coverage_files,
                    source_format=coverage_format or "lcov",
                    ingested_commit_sha=head_sha,
                )

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

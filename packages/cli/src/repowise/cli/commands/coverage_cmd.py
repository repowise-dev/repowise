"""``repowise coverage`` — ingest and inspect test-coverage reports.

Coverage is auto-discovered and ingested during ``repowise init`` /
``repowise update``. This command is the manual path: point it at an
lcov / Cobertura / Clover report (or let it discover one) to populate
per-file line/branch coverage, which clears ``untested_hotspot`` for files
that are tested regardless of where their tests live.
"""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
)


def _resolve_coverage_repo(path: str | None) -> Path:
    """Resolve the repo path for coverage subcommands (workspace-aware)."""
    target = resolve_command_target(path=path)
    target.notice(console, command="coverage")
    if target.is_workspace:
        primary = target.primary_path()
        if primary is None:
            raise click.ClickException("Workspace has no primary repo configured.")
        return primary
    assert target.repo_path is not None
    return target.repo_path


async def _repo_file_keys(session, repo_id: str) -> set[str]:
    """Canonical repo file keys to resolve report paths against.

    Sourced from the persisted health metrics (one row per indexed file);
    these already carry the repo-relative POSIX key the resolver needs.
    """
    from repowise.core.persistence.crud import get_health_metrics

    metrics = await get_health_metrics(session, repo_id)
    return {m.file_path for m in metrics}


@click.group("coverage")
def coverage_group() -> None:
    """Ingest and inspect test-coverage reports."""


@coverage_group.command("add")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
@click.option(
    "--format",
    "coverage_format",
    type=click.Choice(["lcov", "cobertura", "clover", "repowise-json"]),
    default=None,
    help="Force a parser instead of auto-detecting from content.",
)
def coverage_add(paths: tuple[str, ...], repo: str | None, coverage_format: str | None) -> None:
    """Ingest one or more coverage reports (auto-discovers when none given).

    Examples:

        repowise coverage add                      # discover coverage/lcov.info etc.
        repowise coverage add coverage/lcov.info
        repowise coverage add web.lcov api.lcov    # merged hit-wins
    """
    repo_path = _resolve_coverage_repo(repo)
    ensure_repowise_dir(repo_path)

    from repowise.core.analysis.health.coverage import (
        CoverageConfig,
        build_coverage_map,
        discover_artifacts,
    )
    from repowise.core.repo_config import load_repo_config

    cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))

    if paths:
        report_paths = [Path(p) for p in paths]
    else:
        report_paths = discover_artifacts(repo_path, globs=cfg.artifacts or None)
        if not report_paths:
            console.print(
                "[yellow]No coverage report found.[/yellow] Looked for "
                "coverage/lcov.info, **/cobertura.xml, and similar.\n"
                "Generate one (e.g. [cyan]cargo llvm-cov --lcov --output-path "
                "coverage/lcov.info[/cyan]) then re-run, or pass a path:\n"
                "  [cyan]repowise coverage add path/to/lcov.info[/cyan]"
            )
            return
        console.print(
            f"Discovered {len(report_paths)} report(s): "
            + ", ".join(p.name for p in report_paths[:5])
        )

    async def _do() -> None:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import (
            get_repository_by_path,
            save_coverage_files,
        )

        engine = create_engine(get_db_url_for_repo(repo_path))
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo_row = await get_repository_by_path(session, str(repo_path))
            if repo_row is None:
                console.print(
                    "[yellow]No index yet — run `repowise init` once before "
                    "adding coverage.[/yellow]"
                )
                return
            repo_keys = await _repo_file_keys(session, repo_row.id)
            if not repo_keys:
                console.print(
                    "[yellow]No indexed files found — run `repowise init` first.[/yellow]"
                )
                return

            resolved, errors = build_coverage_map(
                repo_path,
                report_paths,
                repo_keys,
                coverage_format=coverage_format or cfg.format,
                strip_prefix=cfg.strip_prefix,
                path_prefix=cfg.path_prefix,
            )
            for path, err in errors:
                console.print(f"[yellow]  {path.name}: {err}[/yellow]")

            if not resolved.files:
                console.print(
                    "[red]No report files mapped to indexed source files.[/red] "
                    "If paths look prefixed (e.g. build/…), set "
                    "[cyan]coverage.strip_prefix[/cyan] in .repowise/config.yaml."
                )
                return

            await save_coverage_files(
                session,
                repo_row.id,
                resolved.files,
                source_format=resolved.source_format or "lcov",
                ingested_commit_sha=getattr(repo_row, "head_commit", None),
            )

            console.print(
                f"[green]Ingested coverage for {resolved.matched} file(s)[/green] "
                f"({resolved.matched_exact} exact, {resolved.matched_suffix} resolved)."
            )
            skipped = resolved.unmatched + resolved.ambiguous
            if skipped:
                sample = ", ".join(skipped[:5])
                console.print(
                    f"[yellow]{len(skipped)} report file(s) did not map to the "
                    f"repo tree[/yellow] (e.g. {sample})."
                )
            console.print(
                "Run [cyan]repowise health[/cyan] to fold coverage into the "
                "defect scores and clear untested-hotspot findings."
            )

    run_async(_do())


@coverage_group.command("contexts")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
@click.option("--limit", type=int, default=20, help="Max per-test records to print.")
def coverage_contexts(paths: tuple[str, ...], repo: str | None, limit: int) -> None:
    """Preview the per-test coverage map from a context-carrying report.

    Opt-in and read-only: reads a coverage.py ``.coverage`` sqlite (written
    with ``coverage run --contexts=test``) or a per-test lcov, then prints
    "test T covers N lines of file F". Nothing is persisted - this is the
    Phase 0 substrate surface, not the ingest path.

    A report produced without contexts degrades loudly rather than showing
    an empty map:

        repowise coverage contexts .coverage
        repowise coverage contexts coverage/per-test.lcov
    """
    repo_path = _resolve_coverage_repo(repo)

    from repowise.core.analysis.health.coverage import (
        parse_contexts_file,
        resolve_test_reports,
    )

    if paths:
        report_paths = [Path(p) for p in paths]
    else:
        default = repo_path / ".coverage"
        if not default.exists():
            console.print(
                "[yellow]No report given and no .coverage found.[/yellow] "
                "Generate one with [cyan]coverage run --contexts=test[/cyan] "
                "then pass its path:\n"
                "  [cyan]repowise coverage contexts .coverage[/cyan]"
            )
            return
        report_paths = [default]

    for report_path in report_paths:
        report = parse_contexts_file(report_path)
        if not report.has_contexts:
            console.print(
                f"[yellow]{report_path.name}: no contexts - aggregate only, "
                f"per-test features unavailable.[/yellow] Regenerate with "
                f"[cyan]coverage run --contexts=test[/cyan] (or per-test lcov)."
            )
            continue

        async def _resolve(rep=report) -> None:
            from repowise.core.persistence import (
                create_engine,
                create_session_factory,
                get_session,
            )
            from repowise.core.persistence.crud import get_repository_by_path

            engine = create_engine(get_db_url_for_repo(repo_path))
            sf = create_session_factory(engine)
            async with get_session(sf) as session:
                repo_row = await get_repository_by_path(session, str(repo_path))
                repo_keys = await _repo_file_keys(session, repo_row.id) if repo_row else set()
            _print_context_records(rep, repo_keys, resolve_test_reports, limit)

        run_async(_resolve())


def _print_context_records(report, repo_keys, resolve_test_reports, limit: int) -> None:
    """Resolve (when an index exists) and print per-test coverage records."""
    if repo_keys:
        resolved = resolve_test_reports(report, repo_keys)
        records = resolved.records
        header = (
            f"[bold]{len(records)} record(s)[/bold] "
            f"({resolved.matched_exact} exact, {resolved.matched_suffix} resolved, "
            f"{resolved.test_files_resolved} test-file(s) mapped)"
        )
        skipped = resolved.unmatched + resolved.ambiguous
    else:
        records = report.records
        header = f"[bold]{len(records)} record(s)[/bold] (raw paths, no index to resolve against)"
        skipped = []

    console.print(f"{header} from [cyan]{report.source_format}[/cyan] contexts:")
    for rec in records[:limit]:
        via = f"  [dim](test file: {rec.test_file})[/dim]" if rec.test_file else ""
        console.print(
            f"  [green]{rec.test_id}[/green] covers "
            f"{len(rec.covered_lines)} line(s) of [cyan]{rec.file_path}[/cyan]{via}"
        )
    if len(records) > limit:
        console.print(f"  [dim]... {len(records) - limit} more[/dim]")
    if skipped:
        sample = ", ".join(skipped[:5])
        console.print(
            f"[yellow]{len(skipped)} source path(s) did not map to the repo "
            f"tree[/yellow] (e.g. {sample})."
        )


@coverage_group.command("status")
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
def coverage_status(repo: str | None) -> None:
    """Show the coverage currently ingested for this repo."""
    repo_path = _resolve_coverage_repo(repo)

    async def _do() -> None:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import (
            get_coverage_summary,
            get_repository_by_path,
        )

        engine = create_engine(get_db_url_for_repo(repo_path))
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo_row = await get_repository_by_path(session, str(repo_path))
            if repo_row is None:
                console.print("[yellow]No index yet — run `repowise init`.[/yellow]")
                return
            summary = await get_coverage_summary(session, repo_row.id)
            if not summary.get("file_count"):
                console.print(
                    "[yellow]No coverage ingested.[/yellow] Run "
                    "[cyan]repowise coverage add[/cyan] or regenerate the index "
                    "with a coverage report present."
                )
                return
            line_pct = summary.get("line_coverage_pct")
            branch_pct = summary.get("branch_coverage_pct")
            lines_str = f"{line_pct:.1f}%" if line_pct is not None else "n/a"
            console.print(
                f"[bold]Coverage[/bold] ({summary.get('source_format') or 'lcov'})\n"
                f"  Files:  {summary['file_count']}\n"
                f"  Lines:  {lines_str}"
            )
            if branch_pct is not None:
                console.print(f"  Branch: {branch_pct:.1f}%")

    run_async(_do())

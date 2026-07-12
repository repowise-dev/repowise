"""``repowise coverage`` — ingest and inspect test-coverage reports.

The single entry point for coverage. ``coverage add`` populates per-file
line/branch coverage (which clears ``untested_hotspot`` for tested files) and,
when a report carries contexts (a coverage.py ``.coverage`` or a per-test
lcov), also builds the per-test "test-to-code" map. ``repowise health`` then
folds in whatever was ingested here - it no longer takes a coverage flag of
its own.
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
    """Ingest coverage (auto-discovers reports when none are given).

    Stores per-file line/branch coverage, and additionally the per-test
    "test-to-code" map when a report carries contexts - a coverage.py
    ``.coverage`` written with ``coverage run --contexts=test``, or a per-test
    lcov. The map is what answers "which tests exercise this change"; reports
    without contexts ingest per-file coverage only.

    Examples:

        repowise coverage add                      # discover lcov.info, .coverage, ...
        repowise coverage add coverage/lcov.info
        repowise coverage add .coverage            # per-test map from coverage.py
        repowise coverage add web.lcov api.lcov    # merged hit-wins
    """
    repo_path = _resolve_coverage_repo(repo)
    ensure_repowise_dir(repo_path)

    from repowise.core.analysis.health.coverage import (
        CoverageConfig,
        build_coverage_map,
        discover_artifacts,
        parse_contexts_file,
        resolve_test_reports,
    )
    from repowise.core.repo_config import load_repo_config

    cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))

    if paths:
        report_paths = [Path(p) for p in paths]
    else:
        report_paths = discover_artifacts(repo_path, globs=cfg.artifacts or None)
        report_paths += _discover_context_reports(repo_path)
        if not report_paths:
            console.print(
                "[yellow]No coverage report found.[/yellow] Looked for "
                "coverage/lcov.info, .coverage, **/cobertura.xml, and similar.\n"
                "Generate one (e.g. [cyan]coverage run --contexts=test -m pytest[/cyan] "
                "or [cyan]cargo llvm-cov --lcov --output-path coverage/lcov.info[/cyan]) "
                "then re-run, or pass a path:\n"
                "  [cyan]repowise coverage add path/to/report[/cyan]"
            )
            return
        console.print(
            f"Discovered {len(report_paths)} report(s): "
            + ", ".join(p.name for p in report_paths[:5])
        )

    # The aggregate parsers read text (lcov/xml/json); the coverage.py
    # ``.coverage`` sqlite only feeds the per-test map, so keep it out of the
    # text-parsing path (its binary bytes would not decode as UTF-8).
    agg_paths = [p for p in report_paths if not _is_sqlite(p)]

    async def _do() -> None:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import (
            get_repository_by_path,
            save_coverage_files,
            save_test_coverage,
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

            head_sha = getattr(repo_row, "head_commit", None)

            # --- Per-file aggregate coverage (lcov / cobertura / clover / json).
            agg_matched = 0
            if agg_paths:
                resolved, errors = build_coverage_map(
                    repo_path,
                    agg_paths,
                    repo_keys,
                    coverage_format=coverage_format or cfg.format,
                    strip_prefix=cfg.strip_prefix,
                    path_prefix=cfg.path_prefix,
                )
                for path, err in errors:
                    console.print(f"[yellow]  {path.name}: {err}[/yellow]")
                if resolved.files:
                    await save_coverage_files(
                        session,
                        repo_row.id,
                        resolved.files,
                        source_format=resolved.source_format or "lcov",
                        ingested_commit_sha=head_sha,
                    )
                    agg_matched = resolved.matched
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

            # --- Per-test map, from any report that carries contexts.
            map_records: list = []
            map_format: str | None = None
            for report_path in report_paths:
                creport = parse_contexts_file(report_path)
                if not creport.has_contexts:
                    # A plain lcov without TN is normal (handled as aggregate
                    # above); a .coverage without contexts is worth calling out
                    # so the user knows why no map was built.
                    if _is_sqlite(report_path):
                        console.print(
                            f"[yellow]{report_path.name}: no per-test contexts "
                            f"(ran without --contexts=test); per-test map "
                            f"skipped.[/yellow]"
                        )
                    continue
                rtc = resolve_test_reports(
                    creport,
                    repo_keys,
                    strip_prefix=cfg.strip_prefix,
                    path_prefix=cfg.path_prefix,
                )
                map_records.extend(rtc.records)
                map_format = map_format or creport.source_format
            if map_records:
                written = await save_test_coverage(
                    session,
                    repo_row.id,
                    map_records,
                    source_format=map_format or "coverage.py",
                    ingested_commit_sha=head_sha,
                )
                dropped = len(map_records) - written
                extra = (
                    f" [yellow]({dropped} dropped: duplicate or over cap)[/yellow]"
                    if dropped
                    else ""
                )
                console.print(
                    f"[green]Built the test-to-code map: {written} test->file "
                    f"record(s).[/green]{extra}"
                )

            if not agg_matched and not map_records:
                console.print(
                    "[red]No report files mapped to indexed source files.[/red] "
                    "If paths look prefixed (e.g. build/…), set "
                    "[cyan]coverage.strip_prefix[/cyan] in .repowise/config.yaml."
                )
                return
            console.print(
                "Run [cyan]repowise health[/cyan] to fold coverage into the defect "
                "scores, or [cyan]repowise coverage status[/cyan] to review it."
            )

    run_async(_do())


_SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_sqlite(path: Path) -> bool:
    """True when *path* is a coverage.py ``.coverage`` sqlite (binary magic)."""
    try:
        with path.open("rb") as fh:
            return fh.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except OSError:
        return False


def _discover_context_reports(repo_path: Path) -> list[Path]:
    """Find per-test coverage artifacts under the repo root.

    Just the coverage.py ``.coverage`` sqlite - it is the only per-test
    artifact identifiable by name (a per-test lcov is indistinguishable from
    an aggregate one without parsing, so those are passed explicitly and get
    context-parsed alongside the aggregate ingest).
    """
    default = repo_path / ".coverage"
    return [default] if default.is_file() else []


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
            get_test_coverage_summary,
        )

        engine = create_engine(get_db_url_for_repo(repo_path))
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo_row = await get_repository_by_path(session, str(repo_path))
            if repo_row is None:
                console.print("[yellow]No index yet — run `repowise init`.[/yellow]")
                return
            summary = await get_coverage_summary(session, repo_row.id)
            map_summary = await get_test_coverage_summary(session, repo_row.id)

            if not summary.get("file_count") and not map_summary.get("pair_count"):
                console.print(
                    "[yellow]No coverage ingested.[/yellow] Run "
                    "[cyan]repowise coverage add[/cyan] for per-file coverage, or "
                    "[cyan]repowise coverage contexts[/cyan] for the per-test map."
                )
                return

            if summary.get("file_count"):
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

            if map_summary.get("pair_count"):
                console.print(
                    f"[bold]Test-to-code map[/bold] ({map_summary.get('source_format') or 'n/a'})\n"
                    f"  Tests:   {map_summary['test_count']}\n"
                    f"  Files:   {map_summary['source_file_count']}\n"
                    f"  Records: {map_summary['pair_count']}"
                )
            else:
                console.print(
                    "[dim]No test-to-code map yet - build one with "
                    "[cyan]repowise coverage contexts[/cyan].[/dim]"
                )

    run_async(_do())

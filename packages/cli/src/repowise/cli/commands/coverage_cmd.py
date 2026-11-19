"""``repowise coverage`` — ingest and inspect test-coverage reports.

The single entry point for coverage. ``coverage add`` populates per-file
line/branch coverage from a text report (which clears ``untested_hotspot`` for
tested files) and, when a report carries contexts (a coverage.py ``.coverage``
or a per-test lcov), also builds the per-test "test-to-code" map. ``repowise health
--recompute`` (or the next ``repowise update``) then folds in whatever was ingested here - it no longer takes a coverage flag of
its own.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.markup import escape

from repowise.cli._setup import configure_cli_logging
from repowise.cli.commands._report_ingest import (
    indexed_file_keys,
    open_ingest_target,
    refuse_partial,
    report_stored_partial,
    report_unmapped,
)
from repowise.cli.commands.coverage_check_cmd import coverage_check
from repowise.cli.commands.coverage_suggest_gates_cmd import coverage_suggest_gates
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    reconcile_schema_best_effort,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.core.analysis.health.coverage import PARSERS as COVERAGE_PARSERS
from repowise.core.analysis.health.coverage.contexts import (
    PER_TEST_COVERAGE_COMMAND,
    is_coverage_data_file,
)


def _resolve_coverage_repo(path: str | None, fmt: str = "table") -> Path:
    """Resolve the repo path for coverage subcommands (workspace-aware)."""
    target = resolve_command_target(path=path)
    return target.narrow(notice_console(fmt), command="coverage", repo_option=False)


#: How a ``coverage add`` run ended, reported as its telemetry ``outcome``. The
#: first two stored coverage; every other one stored nothing, or (``--strict``)
#: stored it and still failed.
INGEST_OUTCOMES = frozenset(
    {
        "ingested",
        "partial",
        "no_report",
        "no_index",
        "mostly_unmapped",
        "misaligned",
        "unreadable",
        "unmapped",
        "strict_unmapped",
    }
)
_STORED = frozenset({"ingested", "partial"})


def _record_ingest_outcome(outcome: str, files_matched: int | None = None) -> None:
    """Report how the run ended and a bucket of the report files that mapped."""
    from repowise.cli.platform import telemetry

    fields: dict[str, str] = {"outcome": outcome}
    if files_matched is not None:
        fields["files_matched_bucket"] = telemetry.bucket_count(files_matched)
    telemetry.add_command_outcome(**fields)


async def _repo_file_keys(session, repo_id: str) -> set[str]:
    """Canonical repo file keys to resolve report paths against.

    Kept as this module's name because ``coverage check`` imports it and
    tests replace it; ``coverage add`` passes it on as the key loader.
    """
    return await indexed_file_keys(session, repo_id)


@click.group("coverage")
def coverage_group() -> None:
    """Ingest and inspect test-coverage reports, and gate changes on them in CI."""


coverage_group.add_command(coverage_check)
coverage_group.add_command(coverage_suggest_gates)


@coverage_group.command("add")
@click.argument("paths", nargs=-1)
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
@click.option(
    "--format",
    "coverage_format",
    type=click.Choice(list(COVERAGE_PARSERS)),
    default=None,
    help="Force a parser instead of auto-detecting from content.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Also fail when some report files did not map to the repo tree.",
)
@click.option(
    "--allow-partial",
    is_flag=True,
    default=False,
    help="Store the coverage even when more than half the report did not map to the "
    "repo tree (flagged partial).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def coverage_add(
    paths: tuple[str, ...],
    repo: str | None,
    coverage_format: str | None,
    strict: bool,
    allow_partial: bool,
    verbose: bool,
) -> None:
    """Ingest coverage (auto-discovers reports when none are given).

    Each PATH is a report path or a glob (quote it, e.g. 'artifacts/**/lcov.info'),
    relative to cwd. PATH=PREFIX prepends PREFIX to the paths inside every report
    it matches, like a {path, path_prefix} entry in coverage.paths. A PATH that
    matches no file is an error.

    With hooks.coverage_reingest: true and the Claude Code repowise hooks
    installed, a successful ingest also adds this repository's coverage
    re-ingest hook to .claude/settings.local.json (removed again when the flag
    is not true).

    Stores per-file line/branch coverage from text reports, and the per-test
    "test-to-code" map when a report carries contexts - a coverage.py
    ``.coverage`` recorded with ``pytest --cov --cov-context=test``, or a per-test
    lcov. The map is what answers "which tests exercise this change". A
    ``.coverage`` gives the map only: it does not record which lines are
    executable, so export it with ``coverage lcov`` for per-file coverage.

    Examples:

        repowise coverage add                      # discover lcov.info, .coverage, ...
        repowise coverage add coverage/lcov.info
        repowise coverage add .coverage            # per-test map from coverage.py
        repowise coverage add web.lcov api.lcov    # merged hit-wins
        repowise coverage add 'artifacts/**/lcov.info'
        repowise coverage add web/coverage/lcov.info=web

    Exits non-zero when nothing was stored, so `coverage add ... || exit 1`
    tells a complete ingest from a no-op. When more than half the report did
    not map to the repo tree it stores nothing and suggests a strip_prefix /
    path_prefix, unless --allow-partial. With --strict it also exits non-zero
    when some report files did not map to the repo tree.
    """
    configure_cli_logging(verbose=verbose)

    repo_path = _resolve_coverage_repo(repo)
    ensure_repowise_dir(repo_path)

    from repowise.core.analysis.health.coverage import (
        CoverageConfig,
        build_coverage_map,
        discover_artifacts,
        expand_report_args,
        measurement_commit,
        oldest_report_time,
        parse_contexts_file,
        prefix_hint,
        report_origin_dir,
        resolve_test_reports,
        suggest_prefixes,
    )
    from repowise.core.repo_config import load_repo_config

    cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))

    report_prefixes: dict[Path, str | None] = {}
    if paths:
        try:
            report_prefixes = expand_report_args(paths, Path())
        except FileNotFoundError as exc:
            raise click.BadParameter(str(exc), param_hint="PATHS") from None
        report_paths = list(report_prefixes)
    else:
        report_paths = discover_artifacts(repo_path, globs=cfg.artifacts or None)
        report_paths += _discover_context_reports(repo_path)
        if not report_paths:
            console.print(
                "[yellow]No coverage report found.[/yellow] Looked for "
                "coverage/lcov.info, .coverage, **/cobertura.xml, and similar.\n"
                f"Generate one (e.g. [cyan]{PER_TEST_COVERAGE_COMMAND}[/cyan], "
                "which needs pytest-cov, "
                "or [cyan]cargo llvm-cov --lcov --output-path coverage/lcov.info[/cyan]) "
                "then re-run, or pass a path:\n"
                "  [cyan]repowise coverage add path/to/report[/cyan]"
            )
            _record_ingest_outcome("no_report")
            raise click.exceptions.Exit(1)
        console.print(
            f"Discovered {len(report_paths)} report(s): "
            + ", ".join(p.name for p in report_paths[:5])
        )

    async def _do() -> tuple[str, int | None]:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import save_coverage_files, save_test_coverage

        url = get_db_url_for_repo(repo_path)
        # An index from an older repowise lacks newer columns; back-fill them.
        await reconcile_schema_best_effort(url)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            target = await open_ingest_target(
                session, repo_path, what="coverage", load_keys=_repo_file_keys
            )
            if target is None:
                return "no_index", None
            repo_row, repo_keys = target.repo, target.file_keys

            # The live HEAD (#1747). A report left over from earlier measures
            # the tree as of its own date, so its stamp moves back to the
            # commit that was HEAD then (#3901, below).
            head_sha = target.head_sha

            # --- Per-file aggregate coverage (any format in COVERAGE_PARSERS).
            # The same read init runs; a ``.coverage`` comes back in *errors*
            # with why it has no per-file coverage, then feeds the map below.
            resolved, errors = build_coverage_map(
                repo_path,
                report_paths,
                repo_keys,
                coverage_format=coverage_format or cfg.format,
                strip_prefix=cfg.strip_prefix,
                path_prefix=cfg.path_prefix,
                report_prefixes=report_prefixes,
                ignore=cfg.ignore,
                allow_partial=allow_partial,
            )
            for path, err in errors:
                console.print(f"[yellow]  {path.name}: {err}[/yellow]")
            if stale := resolved.alignment.warning():
                console.print(f"[yellow]{escape(stale)}[/yellow]")
            # Counted from `resolved`, not from inside the `resolved.files`
            # branch below: total mapping loss leaves `files` empty, so
            # reading the count in there reported 0 report files unmapped
            # for the one run where every single one of them was.
            skipped = resolved.unmatched + resolved.ambiguous
            unmapped = len(skipped)

            # --- Per-test map, from any report that carries contexts.
            map_records: list = []
            map_format: str | None = None
            map_readable = False
            # Reports that gave the map rows, for the stamp below: a leftover
            # artifact with no contexts (or none that mapped) is not a
            # measurement this ingest describes.
            map_paths: list[Path] = []
            map_skipped: list[str] = []
            for report_path in report_paths:
                creport = parse_contexts_file(report_path)
                if not creport.has_contexts:
                    # A plain lcov without TN is normal (handled as aggregate
                    # above); a .coverage without contexts is worth calling out
                    # so the user knows why no map was built.
                    if is_coverage_data_file(report_path):
                        console.print(
                            f"[yellow]{report_path.name}: no per-test contexts "
                            f"(recorded without per-test contexts); per-test map "
                            f"skipped.[/yellow]"
                        )
                    continue
                map_readable = True
                creport.origin_dir = report_origin_dir(report_path, repo_path)
                rtc = resolve_test_reports(
                    creport,
                    repo_keys,
                    strip_prefix=cfg.strip_prefix,
                    path_prefix=report_prefixes.get(report_path) or cfg.path_prefix,
                    ignore=cfg.ignore,
                )
                if rtc.mapping_partial:
                    map_skipped.extend(rtc.unmatched + rtc.ambiguous)
                if rtc.records:
                    map_paths.append(report_path)
                map_records.extend(rtc.records)
                map_format = map_format or creport.source_format
            # Reported as a bucket: the files either leg mapped, so a map-only
            # ``.coverage`` ingest does not read as zero.
            files_matched = max(resolved.matched, len({r.file_path for r in map_records}))

            # A fragment is stored only with --allow-partial: of the per-file
            # coverage or of the map, either one withholds both.
            partial = resolved.mapping_partial or bool(map_skipped)
            if partial and not allow_partial:
                suggested = (
                    resolved.suggested_prefixes
                    if resolved.mapping_partial
                    else suggest_prefixes(
                        map_skipped, repo_keys, mapped={r.file_path for r in map_records}
                    )
                )
                part = "report" if resolved.mapping_partial else "per-test map"
                refuse_partial(part, prefix_hint(suggested))
                return "mostly_unmapped", files_matched

            agg_matched = 0
            # Both writes stamp the commit that was HEAD when their own reports
            # were written (#3901), and each asks only about the files that
            # actually gave it rows: an unreadable or context-free artifact left
            # beside a fresh report must not pull a valid ingest back to the
            # stale one's date. *resolved* carries the aggregate's parsed set,
            # ``map_paths`` the per-test one; an empty set answers ``None`` and
            # keeps HEAD, which is what a report written just now is.
            agg_sha = measurement_commit(repo_path, resolved.measured_at, head_sha)
            map_sha = measurement_commit(repo_path, oldest_report_time(map_paths), head_sha)
            if resolved.files:
                await save_coverage_files(
                    session,
                    repo_row.id,
                    resolved.files,
                    source_format=resolved.source_format or "lcov",
                    ingested_commit_sha=agg_sha,
                    provenance=resolved.provenance,
                )
                agg_matched = resolved.matched
                console.print(
                    f"[green]Ingested coverage for {resolved.matched} file(s)[/green] "
                    f"({resolved.matched_exact} exact, {resolved.matched_suffix} resolved)."
                )
                report_unmapped(skipped, "report file(s)")
            if map_records:
                saved = await save_test_coverage(
                    session,
                    repo_row.id,
                    map_records,
                    source_format=map_format or "coverage.py",
                    ingested_commit_sha=map_sha,
                )
                extra = f" ({saved.merged} repeated record(s) merged)" if saved.merged else ""
                console.print(
                    f"[green]Built the test-to-code map: {saved.written} test->file "
                    f"record(s).[/green]{extra}"
                )
                if saved.truncated:
                    sample = ", ".join(saved.dropped_sample)
                    console.print(
                        f"[yellow]The map is at its row cap: {len(saved.dropped_tests)} "
                        f"test(s) left out whole (e.g. {escape(sample)}). Test selection "
                        "runs every test while the map is truncated.[/yellow]"
                    )
            if partial:
                report_stored_partial("coverage")

            if not agg_matched and not map_records:
                if resolved.alignment.reasons:
                    # Every mapped file was dropped as misaligned; the warning above says why.
                    console.print("[red]Nothing was stored.[/red]")
                    return "misaligned", files_matched
                if len(errors) == len(report_paths) and not map_readable:
                    # No report was readable as coverage; the lines above say why.
                    console.print("[red]Nothing was stored.[/red]")
                    return "unreadable", files_matched
                console.print(
                    "[red]No report files mapped to indexed source files.[/red] "
                    "If paths look prefixed (e.g. build/…), set "
                    "[cyan]coverage.strip_prefix[/cyan] in .repowise/config.yaml."
                )
                return "unmapped", files_matched
            if strict and unmapped:
                console.print(
                    f"[red]--strict: {unmapped} report file(s) did not map to the repo tree.[/red]"
                )
                return "strict_unmapped", files_matched
            console.print(
                "Run [cyan]repowise health --recompute[/cyan] to fold coverage into the defect "
                "scores, or [cyan]repowise coverage status[/cyan] to review it."
            )
            return ("partial" if partial else "ingested"), files_matched

    # A refresh is commonly scripted as `coverage add ... || exit 1`, so a run
    # that stored nothing has to be distinguishable from a complete one by its
    # exit status alone.
    outcome, files_matched = run_async(_do())
    _record_ingest_outcome(outcome, files_matched)
    if outcome not in _STORED:
        raise click.exceptions.Exit(1)
    from repowise.cli.commands.augment_cmd.coverage_reingest import sync_repo_hook

    sync_repo_hook(repo_path, console)


def _discover_context_reports(repo_path: Path) -> list[Path]:
    """Find per-test coverage artifacts under the repo root.

    Just the coverage.py ``.coverage`` sqlite - it is the only per-test
    artifact identifiable by name (a per-test lcov is indistinguishable from
    an aggregate one without parsing, so those are passed explicitly and get
    context-parsed alongside the aggregate ingest).
    """
    default = repo_path / ".coverage"
    return [default] if default.is_file() else []


@coverage_group.command("setup")
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
@format_option()
def coverage_setup(repo: str | None, fmt: str) -> None:
    """Print how to collect coverage here (the coverage part of `evidence setup`).

    Per build unit: the test command with coverage, what it needs first, where
    the report lands and the exact `repowise coverage add` line. Writes nothing.
    """
    from repowise.cli.commands.evidence_cmd import _print_setup

    _print_setup(repo, fmt, coverage_only=True)


@coverage_group.command("status")
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
# Safe to spell this ``--format`` here: the ``--format`` that names an *input*
# parser (a COVERAGE_PARSERS key) lives on ``coverage add``, not on the
# group, so the two never meet on one command line.
@format_option()
def coverage_status(repo: str | None, fmt: str) -> None:
    """Show the coverage currently ingested for this repo."""
    repo_path = _resolve_coverage_repo(repo, fmt)
    notices = notice_console(fmt)

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

        url = get_db_url_for_repo(repo_path)
        # An index from an older repowise lacks newer columns; back-fill them.
        await reconcile_schema_best_effort(url)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo_row = await get_repository_by_path(session, str(repo_path))
            if repo_row is None:
                notices.print("[yellow]No index yet. Run `repowise init`.[/yellow]")
                if fmt == "json":
                    emit_json({"repo": str(repo_path), "indexed": False})
                return
            summary = await get_coverage_summary(
                session, repo_row.id, reference_commit=repo_row.head_commit
            )
            map_summary = await get_test_coverage_summary(session, repo_row.id)

            if fmt == "json":
                emit_json(
                    {
                        "repo": str(repo_path),
                        "indexed": True,
                        "coverage": summary if summary.get("file_count") else None,
                        "test_map": map_summary if map_summary.get("pair_count") else None,
                    }
                )
                return

            if not summary.get("file_count") and not map_summary.get("pair_count"):
                console.print(
                    "[yellow]No coverage ingested.[/yellow] Run "
                    "[cyan]repowise coverage add <report>[/cyan]. A .coverage file "
                    f"recorded with [cyan]{PER_TEST_COVERAGE_COMMAND}[/cyan] (needs pytest-cov) "
                    "also builds the per-test map."
                )
                return

            if summary.get("file_count"):
                line_pct = summary.get("line_coverage_pct")
                branch_pct = summary.get("branch_coverage_pct")
                lines_str = f"{line_pct:.1f}%" if line_pct is not None else "n/a"
                partial = summary.get("mapping_partial")
                partial_suffix = (
                    " [red](partial: stored numbers cover a fragment of the repo "
                    "; fix coverage.strip_prefix and re-ingest)[/red]"
                    if partial
                    else ""
                )
                console.print(
                    f"[bold]Coverage[/bold] ({summary.get('source_format') or 'lcov'})\n"
                    f"  Files:  {summary['file_count']}\n"
                    f"  Lines:  {lines_str}{partial_suffix}"
                )
                if branch_pct is not None:
                    console.print(f"  Branch: {branch_pct:.1f}%")
                paths = summary.get("report_paths")
                if paths:
                    console.print(
                        f"  Report paths matched: {paths['matched']} of {paths['total']}"
                    )
                if (summary.get("freshness") or {}).get("status") == "stale":
                    console.print(
                        f"  [yellow]Measured at {(summary['ingested_commit_sha'] or '')[:8]}, "
                        "not the indexed commit; re-run the tests and ingest again.[/yellow]"
                    )

            if map_summary.get("pair_count"):
                console.print(
                    f"[bold]Test-to-code map[/bold] ({map_summary.get('source_format') or 'n/a'})\n"
                    f"  Tests:   {map_summary['test_count']}\n"
                    f"  Files:   {map_summary['source_file_count']}\n"
                    f"  Records: {map_summary['pair_count']}"
                )
            elif summary.get("source_format") == "apex":
                # test-result-codecoverage.json is per class only; the run's
                # own result file carries the per-test records.
                console.print(
                    "[dim]No test-to-code map yet - add the run's "
                    "[cyan]test-result-<run id>.json[/cyan] from "
                    "[cyan]sf apex run test --code-coverage --output-dir <dir>[/cyan] with "
                    "[cyan]repowise coverage add[/cyan].[/dim]"
                )
            else:
                console.print(
                    "[dim]No test-to-code map yet - record one with "
                    f"[cyan]{PER_TEST_COVERAGE_COMMAND}[/cyan] (needs pytest-cov), then "
                    "[cyan]repowise coverage add .coverage[/cyan].[/dim]"
                )

    run_async(_do())


@coverage_group.command("export")
@click.argument(
    "report", required=False, type=click.Path(exists=True, dir_okay=False, readable=True)
)
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
@click.option(
    "--output",
    "-o",
    "output",
    default="per-test-map.json.gz",
    show_default=True,
    help="File to write the gzipped map to.",
)
def coverage_export(report: str | None, repo: str | None, output: str) -> None:
    """Write the per-test map as a small gzipped file, for an upload from CI.

    With no REPORT, the map `coverage add` stored; with one (a `.coverage` with
    per-test contexts, or a per-test lcov), that report's map, no index needed.
    Either way in the stored layout: a few MB where the raw database is
    gigabytes to parse.
    """
    from repowise.core.persistence.crud.analysis.coverage_map import (
        dump_per_test_map,
        per_test_map_from_records,
    )

    async def _stored():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import get_repository_by_path
        from repowise.core.persistence.crud.analysis.coverage_map import get_per_test_map

        repo_path = _resolve_coverage_repo(repo)
        engine = create_engine(get_db_url_for_repo(repo_path))
        async with get_session(create_session_factory(engine)) as session:
            repo_row = await get_repository_by_path(session, str(repo_path))
            return await get_per_test_map(session, repo_row.id) if repo_row else None

    if report:
        from repowise.core.analysis.health.coverage.contexts import parse_contexts_file

        parsed = parse_contexts_file(report)
        tmap = (
            per_test_map_from_records(parsed.records, parsed.source_format)
            if parsed.has_contexts
            else None
        )
    else:
        tmap = run_async(_stored())
    if tmap is None:
        console.print(
            "[yellow]No per-test map to export.[/yellow] Record one with "
            f"[cyan]{PER_TEST_COVERAGE_COMMAND}[/cyan], then "
            "[cyan]repowise coverage export .coverage[/cyan]."
        )
        raise click.exceptions.Exit(1)
    data = dump_per_test_map(tmap)
    Path(output).write_bytes(data)
    console.print(
        f"Wrote {escape(output)}: {len(tmap.tests)} tests, {tmap.pair_count} test-file pairs, "
        f"{len(data) / 1e6:.1f} MB."
    )

"""``repowise health`` — code-health biomarker report.

Mirrors the dead-code CLI: ingest → analyze → render. Reads from
``HealthFileMetric`` / ``HealthFinding`` if a fresh index exists, falls
back to a live in-process analysis when run outside an indexed repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    err_console,
    load_state,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output,
)


@click.command("health")
@click.argument("path", required=False, type=click.Path(exists=True))
@click.option(
    "--file",
    "file_filter",
    default=None,
    help="Deep-dive a single file (relative path).",
)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json", "md"]),
    help="Output format.",
)
@click.option(
    "--safe-only",
    is_flag=True,
    default=False,
    help="Phase-3 placeholder — currently a no-op for v1 biomarkers.",
)
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Workspace repo alias to analyze.",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode.",
)
@click.option(
    "--coverage",
    "coverage_paths",
    multiple=True,
    type=click.Path(exists=True),
    help="Ingest a coverage report (LCOV/Cobertura/Clover). May be repeated.",
)
@click.option(
    "--coverage-format",
    "coverage_format",
    default=None,
    type=click.Choice(["lcov", "cobertura", "clover"]),
    help="Override coverage-format auto-detection.",
)
@click.option(
    "--refactoring-targets",
    "refactoring_targets",
    is_flag=True,
    default=False,
    help="Print top refactoring candidates (impact/effort ratio).",
)
@click.option(
    "--module",
    "module_filter",
    default=None,
    help="Restrict the report to files whose path starts with this prefix.",
)
@click.option(
    "--trend",
    "trend_view",
    is_flag=True,
    default=False,
    help="Print the last 10 health snapshots from the SQLite history.",
)
def health_command(
    path: str | None,
    file_filter: str | None,
    fmt: str,
    safe_only: bool,
    repo_alias: str | None,
    no_workspace: bool,
    coverage_paths: tuple[str, ...],
    coverage_format: str | None,
    refactoring_targets: bool,
    module_filter: str | None,
    trend_view: bool,
) -> None:
    """Compute code-health scores from biomarkers (CCN, nesting, brain-method).

    Runs in-process — no LLM, no network. Re-uses the repowise ingestion
    parser, graph builder, and git indexer.
    """
    from pathlib import Path as PathlibPath

    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.analysis.health.coverage import parse as parse_coverage
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    # Silence structlog/stdlib info+debug lines when the user asked for a
    # machine-readable format so stdout is pure JSON/Markdown and safe to
    # pipe into jq or other tools (e.g. `repowise health --format json | jq .kpis`).
    if fmt != "table":
        silence_logs_for_machine_output()

    # Status output goes to stderr when the user asked for a machine-readable
    # format — otherwise rich's banner pollutes stdout and breaks
    # `repowise health --format json | jq …` (and the CI smoke test).
    status = err_console if fmt != "table" else console

    target = resolve_command_target(
        path=path, no_workspace_flag=no_workspace, repo_alias=repo_alias
    )
    target.notice(status, command="health")

    if target.is_workspace:
        if target.repo_filter is not None:
            picked = target.resolve_repo_alias(target.repo_filter)
            if picked is None:
                raise click.ClickException(f"Unknown repo alias: {target.repo_filter}")
            repo_path = picked
        else:
            primary = target.primary_path()
            if primary is None:
                raise click.ClickException("Workspace has no primary repo configured.")
            repo_path = primary
    else:
        assert target.repo_path is not None
        repo_path = target.repo_path

    status.print(f"[bold]repowise health[/bold] — {repo_path}")

    if trend_view:
        _render_trend(repo_path, fmt=fmt)
        return

    # Analyze the same file set that was indexed: a repo initialized with
    # --include-submodules persists the flag in state.json, and a flagless
    # traverser here would silently score a different (smaller) tree.
    state = load_state(repo_path)
    include_submodules = bool(state.get("include_submodules", False))
    include_nested_repos = bool(state.get("include_nested_repos", False))

    traverser = FileTraverser(
        repo_path,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    file_infos = list(traverser.traverse())
    parser = ASTParser()
    graph_builder = GraphBuilder(
        repo_path,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )

    parsed_files = []
    for fi in file_infos:
        try:
            source = PathlibPath(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            graph_builder.add_file(parsed)
            parsed_files.append(parsed)
        except Exception:
            continue
    graph_builder.build()

    git_meta_map: dict = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        git_indexer = GitIndexer(repo_path)
        _, metadata_list = run_async(git_indexer.index_repo(""))
        git_meta_map = {m["file_path"]: m for m in metadata_list}
    except Exception:
        pass

    coverage_map: dict[str, dict] = {}
    coverage_persist_files: list = []
    coverage_persist_format: str | None = None
    if coverage_paths:
        for cov_path in coverage_paths:
            try:
                text = PathlibPath(cov_path).read_text(encoding="utf-8")
            except OSError as exc:
                status.print(f"[red]Could not read coverage file {cov_path}: {exc}[/red]")
                continue
            report_cov = parse_coverage(text, format=coverage_format)
            if not report_cov.files:
                status.print(
                    f"[yellow]No coverage entries parsed from {cov_path} "
                    f"(detected={report_cov.source_format}).[/yellow]"
                )
                continue
            for fc in report_cov.files:
                coverage_map[fc.file_path] = {
                    "line_coverage_pct": fc.line_coverage_pct,
                    "branch_coverage_pct": fc.branch_coverage_pct,
                    "covered_lines": list(fc.covered_lines),
                    "total_coverable_lines": fc.total_coverable_lines,
                    "source_format": report_cov.source_format,
                }
            # Accumulate for DB persistence. Last format wins when multiple
            # reports are passed — they should all be the same format in
            # practice, but the CLI doesn't enforce it.
            coverage_persist_files.extend(report_cov.files)
            coverage_persist_format = report_cov.source_format
            status.print(
                f"[green]Ingested {len(report_cov.files)} files "
                f"from {cov_path} ({report_cov.source_format}).[/green]"
            )

    analyzer = HealthAnalyzer(
        graph_builder.graph(),
        git_meta_map=git_meta_map,
        parsed_files=parsed_files,
        coverage_map=coverage_map,
        duplication_cache_dir=Path(repo_path) / ".repowise",
    )
    # Load any .repowise/health-rules.json the user keeps in the repo.
    from repowise.core.analysis.health.config import HealthConfig

    health_cfg = HealthConfig.load(repo_path)
    analyzer_cfg = (
        health_cfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])
        if (health_cfg.disabled_biomarkers or health_cfg.rules)
        else None
    )
    report = analyzer.analyze(analyzer_cfg)

    # Persist health + coverage to the repo's wiki.db so the dashboard,
    # MCP tools, and `repowise status` see the same numbers as this CLI
    # run. Without this step, `--coverage` was effectively a stdout-only
    # toy — biomarkers got recomputed in memory but nothing reached the
    # tables that drive the Coverage page / get_health.
    #
    # Skip when fmt != "table" (json/md are read by scripts and CI; side
    # effects are unwelcome) or when the run is filtered to a single
    # file/module (those are inspection runs that shouldn't overwrite
    # repo-level state).
    if fmt == "table" and not file_filter and not module_filter:
        _persist_health(
            repo_path,
            report=report,
            coverage_files=coverage_persist_files,
            coverage_format=coverage_persist_format,
        )

    metrics = report.metrics
    if file_filter:
        metrics = [m for m in metrics if m.file_path == file_filter]
    if module_filter:
        metrics = [m for m in metrics if m.file_path.startswith(module_filter)]
    metrics_sorted = sorted(metrics, key=lambda m: m.score)

    findings = report.findings
    if file_filter:
        findings = [f for f in findings if f.file_path == file_filter]
    if module_filter:
        findings = [f for f in findings if f.file_path.startswith(module_filter)]

    if refactoring_targets:
        _render_refactoring_targets(metrics_sorted, findings, fmt=fmt)
        return

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "kpis": report.kpis,
                    "metrics": [
                        {
                            "file_path": m.file_path,
                            "score": m.score,
                            "max_ccn": m.max_ccn,
                            "max_nesting": m.max_nesting,
                            "nloc": m.nloc,
                            "has_test_file": m.has_test_file,
                            "line_coverage_pct": m.line_coverage_pct,
                            "branch_coverage_pct": m.branch_coverage_pct,
                            "duplication_pct": m.duplication_pct,
                        }
                        for m in metrics_sorted
                    ],
                    "findings": [
                        {
                            "biomarker_type": f.biomarker_type,
                            "severity": str(f.severity),
                            "file_path": f.file_path,
                            "function_name": f.function_name,
                            "health_impact": f.health_impact,
                            "details": f.details,
                            "reason": f.reason,
                        }
                        for f in findings
                    ],
                },
                indent=2,
            )
        )
        return

    if fmt == "md":
        click.echo("# Code Health Report\n")
        for k, v in report.kpis.items():
            click.echo(f"- **{k}**: {v}")
        click.echo("\n## Findings\n")
        for f in findings:
            click.echo(
                f"- [{f.severity}] `{f.file_path}` {f.function_name or ''} "
                f"- {f.reason} (impact -{f.health_impact:.2f})"
            )
        return

    # Table format
    kpis = report.kpis
    console.print(
        f"\nHotspot: [bold]{kpis.get('hotspot_health', '?')}[/bold]/10 · "
        f"Average: [bold]{kpis.get('average_health', '?')}[/bold]/10 · "
        f"Worst: [bold]{kpis.get('worst_performer_score', '?')}[/bold]/10 "
        f"({kpis.get('worst_performer_path', 'n/a')})\n"
    )

    table = Table(title=f"Lowest-scoring files ({min(len(metrics_sorted), 20)})")
    table.add_column("File", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("CCN", justify="right")
    table.add_column("Nest", justify="right")
    table.add_column("NLOC", justify="right")
    table.add_column("Test?", justify="center")
    for m in metrics_sorted[:20]:
        score_color = "red" if m.score < 4 else "yellow" if m.score < 7 else "green"
        table.add_row(
            m.file_path,
            f"[{score_color}]{m.score:.1f}[/{score_color}]",
            str(m.max_ccn),
            str(m.max_nesting),
            str(m.nloc),
            "✓" if m.has_test_file else "—",
        )
    console.print(table)

    if findings:
        console.print(f"\n[bold]{len(findings)}[/bold] biomarker findings:")
        f_table = Table()
        f_table.add_column("Severity", style="magenta")
        f_table.add_column("Biomarker", style="cyan")
        f_table.add_column("File")
        f_table.add_column("Function")
        f_table.add_column("Impact", justify="right")
        for f in findings[:30]:
            f_table.add_row(
                str(f.severity),
                f.biomarker_type,
                f.file_path,
                f.function_name or "-",
                f"-{f.health_impact:.2f}",
            )
        console.print(f_table)


def _effort_bucket(nloc: int) -> tuple[str, int]:
    if nloc <= 40:
        return "S", 1
    if nloc <= 150:
        return "M", 2
    if nloc <= 400:
        return "L", 3
    return "XL", 5


def _render_refactoring_targets(
    metrics: list, findings: list, *, fmt: str, limit: int = 20
) -> None:
    """Aggregate findings per file, rank by impact/effort, render."""
    by_file: dict[str, list] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)

    metric_by_path = {m.file_path: m for m in metrics}
    targets: list[dict] = []
    for path, fs in by_file.items():
        m = metric_by_path.get(path)
        nloc = m.nloc if m is not None else 0
        score = m.score if m is not None else 10.0
        primary = max(fs, key=lambda x: x.health_impact)
        total_impact = round(sum(x.health_impact for x in fs), 3)
        bucket, weight = _effort_bucket(nloc)
        targets.append(
            {
                "file_path": path,
                "score": round(score, 2),
                "nloc": nloc,
                "primary_biomarker": primary.biomarker_type,
                "primary_severity": str(primary.severity),
                "primary_reason": primary.reason,
                "total_impact": total_impact,
                "effort_bucket": bucket,
                "impact_per_effort": round(total_impact / weight, 3),
                "finding_count": len(fs),
            }
        )
    targets.sort(key=lambda t: (-t["impact_per_effort"], -t["total_impact"]))
    targets = targets[:limit]

    if fmt == "json":
        click.echo(json.dumps({"targets": targets}, indent=2))
        return
    if fmt == "md":
        click.echo("# Refactoring targets\n")
        for t in targets:
            click.echo(
                f"- **{t['file_path']}** ({t['effort_bucket']}, "
                f"score {t['score']:.1f}/10, -{t['total_impact']:.2f}) "
                f"— {t['primary_biomarker']}: {t['primary_reason']}"
            )
        return

    table = Table(title=f"Refactoring targets ({len(targets)})")
    table.add_column("File", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Impact", justify="right")
    table.add_column("Effort", justify="center")
    table.add_column("Ratio", justify="right")
    table.add_column("Primary biomarker")
    for t in targets:
        table.add_row(
            t["file_path"],
            f"{t['score']:.1f}",
            f"-{t['total_impact']:.2f}",
            t["effort_bucket"],
            f"{t['impact_per_effort']:.2f}",
            t["primary_biomarker"],
        )
    console.print(table)


def _render_trend(repo_path: object, *, fmt: str) -> None:
    """Print the last 10 health snapshots straight from SQLite history.

    Reads through the existing CLI db-url helper so this works on
    workspace and single-repo indexes alike. When no snapshots exist
    (e.g. health was never run), prints a friendly hint.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.analysis.health.trends import diff_snapshots, recent_kpis
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        list_health_snapshots,
    )

    async def _fetch() -> tuple[list[dict], object]:
        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return [], None
            snaps = await list_health_snapshots(session, repo.id)
            return recent_kpis(snaps, limit=10), diff_snapshots(snaps)

    rows, summary = run_async(_fetch())
    if not rows:
        console.print(
            "[yellow]No health snapshots yet. Run `repowise init` or `repowise health` "
            "to populate history.[/yellow]"
        )
        return

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "recent": rows,
                    "alerts": [
                        {
                            "kind": a.kind,
                            "metric": a.metric,
                            "current": a.current,
                            "baseline": a.baseline,
                            "delta": a.delta,
                            "message": a.message,
                        }
                        for a in (summary.alerts if summary else [])
                    ],
                },
                indent=2,
            )
        )
        return

    table = Table(title="Code-health snapshots (newest first)")
    table.add_column("Taken at")
    table.add_column("Hotspot", justify="right")
    table.add_column("Average", justify="right")
    table.add_column("Worst", justify="right")
    table.add_column("Worst file", style="dim")
    for r in rows:
        table.add_row(
            (r["taken_at"] or "—")[:19],
            f"{r['hotspot_health']:.2f}",
            f"{r['average_health']:.2f}",
            f"{r['worst_performer_score']:.2f}" if r["worst_performer_score"] is not None else "—",
            r["worst_performer_path"] or "—",
        )
    console.print(table)

    if summary and summary.alerts:
        console.print()
        for a in summary.alerts:
            color = "red" if a.kind == "declining" else "yellow"
            console.print(f"[{color}]⚠ {a.kind}[/{color}]: {a.message}")


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
                    per_file_scores={
                        m.file_path: round(float(m.score), 2) for m in metrics
                    },
                )
            except Exception as exc:
                console.print(f"[yellow]Snapshot write skipped: {exc}[/yellow]")

            await session.commit()

    try:
        run_async(_do())
    except Exception as exc:
        console.print(f"[red]Could not persist health to DB: {exc}[/red]")

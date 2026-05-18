"""``repowise health`` — code-health biomarker report.

Mirrors the dead-code CLI: ingest → analyze → render. Reads from
``HealthFileMetric`` / ``HealthFinding`` if a fresh index exists, falls
back to a live in-process analysis when run outside an indexed repo.
"""

from __future__ import annotations

import json

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    resolve_command_target,
    run_async,
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
def health_command(
    path: str | None,
    file_filter: str | None,
    fmt: str,
    safe_only: bool,
    repo_alias: str | None,
    no_workspace: bool,
    coverage_paths: tuple[str, ...],
    coverage_format: str | None,
) -> None:
    """Compute code-health scores from biomarkers (CCN, nesting, brain-method).

    Runs in-process — no LLM, no network. Re-uses the repowise ingestion
    parser, graph builder, and git indexer.
    """
    from pathlib import Path as PathlibPath

    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.analysis.health.coverage import parse as parse_coverage
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    target = resolve_command_target(
        path=path, no_workspace_flag=no_workspace, repo_alias=repo_alias
    )
    target.notice(console, command="health")

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

    console.print(f"[bold]repowise health[/bold] — {repo_path}")

    traverser = FileTraverser(repo_path)
    file_infos = list(traverser.traverse())
    parser = ASTParser()
    graph_builder = GraphBuilder(repo_path)

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
    if coverage_paths:
        for cov_path in coverage_paths:
            try:
                text = PathlibPath(cov_path).read_text(encoding="utf-8")
            except OSError as exc:
                console.print(f"[red]Could not read coverage file {cov_path}: {exc}[/red]")
                continue
            report_cov = parse_coverage(text, format=coverage_format)
            if not report_cov.files:
                console.print(
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
            console.print(
                f"[green]Ingested {len(report_cov.files)} files "
                f"from {cov_path} ({report_cov.source_format}).[/green]"
            )

    analyzer = HealthAnalyzer(
        graph_builder.graph(),
        git_meta_map=git_meta_map,
        parsed_files=parsed_files,
        coverage_map=coverage_map,
    )
    report = analyzer.analyze()

    metrics = report.metrics
    if file_filter:
        metrics = [m for m in metrics if m.file_path == file_filter]
    metrics_sorted = sorted(metrics, key=lambda m: m.score)

    findings = report.findings
    if file_filter:
        findings = [f for f in findings if f.file_path == file_filter]

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

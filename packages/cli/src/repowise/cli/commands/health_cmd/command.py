"""``repowise health`` Click command + single-repo orchestration.

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

from .codegen import _generate_refactoring_code
from .persist import _load_persisted_coverage_map, _persist_health
from .refactoring_targets import _render_refactoring_targets
from .summary import (
    _render_badge,
    _render_defect_accuracy_line,
    _render_distribution_line,
    _render_performance_section,
)
from .trends import _render_trend


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
    help="Phase-3 placeholder — currently a no-op for v1 markers.",
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
    "--refactoring-targets",
    "refactoring_targets",
    is_flag=True,
    default=False,
    help="Print top refactoring candidates (impact/effort ratio).",
)
@click.option(
    "--generate-code",
    "generate_code",
    default=None,
    metavar="SELECTOR",
    help=(
        "Opt-in: generate refactored code + a diff for one suggestion via the "
        "configured LLM. SELECTOR is a 1-based rank (e.g. 1) or a target-symbol "
        "match. Reuses the repo's provider/model; requires an API key."
    ),
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
@click.option(
    "--badge",
    "badge_view",
    is_flag=True,
    default=False,
    help="Print a ready-to-paste health badge (Markdown) for this repo's README.",
)
def health_command(
    path: str | None,
    file_filter: str | None,
    fmt: str,
    safe_only: bool,
    repo_alias: str | None,
    no_workspace: bool,
    refactoring_targets: bool,
    generate_code: str | None,
    module_filter: str | None,
    trend_view: bool,
    badge_view: bool,
) -> None:
    """Compute code-health scores from markers (CCN, nesting, brain-method).

    Runs in-process — no LLM, no network. Re-uses the repowise ingestion
    parser, graph builder, and git indexer.
    """
    from pathlib import Path as PathlibPath

    from repowise.core.analysis.health import HealthAnalyzer
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

    from repowise.core.ingestion import wire_tsconfig_resolver

    wire_tsconfig_resolver(
        graph_builder,
        repo_path,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    graph_builder.build()

    git_meta_map: dict = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        git_indexer = GitIndexer(repo_path)
        _, metadata_list = run_async(git_indexer.index_repo(""))
        git_meta_map = {m["file_path"]: m for m in metadata_list}
    except Exception:
        pass

    # Coverage folds into scoring from whatever `repowise coverage add` (or
    # index-time ingest) persisted - no per-run flag. Ingestion lives solely
    # in the `coverage` command group.
    coverage_map = _load_persisted_coverage_map(repo_path)

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

    # Persist health to the repo's wiki.db so the dashboard, MCP tools, and
    # `repowise status` see the same numbers as this CLI run.
    #
    # Skip when fmt != "table" (json/md are read by scripts and CI; side
    # effects are unwelcome) or when the run is filtered to a single
    # file/module (those are inspection runs that shouldn't overwrite
    # repo-level state).
    if fmt == "table" and not file_filter and not module_filter:
        _persist_health(repo_path, report=report)

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

    if generate_code is not None:
        suggestions = getattr(report, "refactoring_suggestions", None) or []
        if file_filter:
            suggestions = [s for s in suggestions if s.file_path == file_filter]
        if module_filter:
            suggestions = [s for s in suggestions if s.file_path.startswith(module_filter)]
        _generate_refactoring_code(repo_path, suggestions, generate_code, fmt=fmt)
        return

    if refactoring_targets:
        suggestions = getattr(report, "refactoring_suggestions", None) or []
        if file_filter:
            suggestions = [s for s in suggestions if s.file_path == file_filter]
        if module_filter:
            suggestions = [s for s in suggestions if s.file_path.startswith(module_filter)]
        _render_refactoring_targets(metrics_sorted, findings, suggestions, fmt=fmt)
        return

    if badge_view:
        _render_badge(report.kpis.get("average_health"))
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
    from repowise.core.analysis.health.grading import (
        BAND_LABEL,
        band_for,
    )
    from repowise.core.analysis.health.grading import (
        distribution as health_distribution,
    )

    kpis = report.kpis
    avg = kpis.get("average_health")
    band_str = ""
    if isinstance(avg, (int, float)):
        band = band_for(float(avg))
        band_color = {"healthy": "green", "warning": "yellow", "alert": "red"}[band]
        band_str = f" [[{band_color}]{BAND_LABEL[band]}[/{band_color}]]"
    console.print(
        f"\nHotspot: [bold]{kpis.get('hotspot_health', '?')}[/bold]/10 · "
        f"Average: [bold]{avg if avg is not None else '?'}[/bold]/10{band_str} · "
        f"Worst: [bold]{kpis.get('worst_performer_score', '?')}[/bold]/10 "
        f"({kpis.get('worst_performer_path', 'n/a')})"
    )
    _render_distribution_line(health_distribution(report.metrics))

    _render_defect_accuracy_line(report)

    # Performance pillar section: lead with the finding COUNT + density +
    # coverage (the honest signal), not the bounded /10 average. Language comes
    # from the parsed files (the in-memory metrics don't carry it).
    _render_performance_section(
        report,
        {pf.file_info.path: pf.file_info.language for pf in parsed_files},
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
        console.print(f"\n[bold]{len(findings)}[/bold] marker findings:")
        f_table = Table()
        f_table.add_column("Severity", style="magenta")
        f_table.add_column("Marker", style="cyan")
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

"""``repowise health`` — code-health biomarker report.

Mirrors the dead-code CLI: ingest → analyze → render. Reads from
``HealthFileMetric`` / ``HealthFinding`` if a fresh index exists, falls
back to a live in-process analysis when run outside an indexed repo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    coverage_paths: tuple[str, ...],
    coverage_format: str | None,
    refactoring_targets: bool,
    generate_code: str | None,
    module_filter: str | None,
    trend_view: bool,
    badge_view: bool,
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


def _render_distribution_line(dist: dict) -> None:
    """One compact line: the NLOC-weighted file split across the 3 bands."""
    bands = dist.get("bands") or {}
    if not dist.get("total_files"):
        return
    parts = []
    for band, color in (("healthy", "green"), ("warning", "yellow"), ("alert", "red")):
        share = bands.get(band) or {}
        parts.append(
            f"[{color}]{share.get('pct', 0)}%[/{color}] {band} "
            f"([dim]{share.get('files', 0)} files[/dim])"
        )
    console.print("[dim]Distribution (by code volume):[/dim] " + " · ".join(parts) + "\n")


def _render_badge(average_health: object) -> None:
    """Print ready-to-paste health-badge Markdown for a README.

    Emits a static shields badge for the current score (immediately usable) and
    documents the live endpoint form for a running Repowise server / hosted repo.
    """
    from repowise.core.analysis.health.grading import band_for

    if not isinstance(average_health, (int, float)):
        console.print("[yellow]No health score yet — run `repowise health` first.[/yellow]")
        return
    band = band_for(float(average_health))
    color = {"healthy": "brightgreen", "warning": "yellow", "alert": "red"}[band]
    msg = f"{float(average_health):.1f}/10"
    static = f"https://img.shields.io/badge/health-{msg.replace('/', '%2F')}-{color}"
    console.print("[bold]Static badge (current score):[/bold]")
    console.print(f"  ![code health]({static})")
    console.print("\n[bold]Live badge[/bold] [dim](running Repowise server or hosted repo):[/dim]")
    console.print(
        "  ![code health](https://img.shields.io/endpoint?url="
        "<SERVER>/api/repos/<REPO_ID>/health/badge.json)"
    )


def _render_defect_accuracy_line(report: Any) -> None:
    """One-line "does the score find the bugs?" validation, or nothing.

    Silent when there isn't enough history for an honest number (the core
    compute returns ``None``).
    """
    try:
        from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy

        stat = compute_defect_accuracy(report.metrics, report.findings)
    except Exception:
        return
    if not stat:
        return

    months = max(1, round(stat["window_days"] / 30))
    window = "month" if months == 1 else f"{months} months"
    line = (
        f"[dim]Does the score find the bugs? [/dim]"
        f"[bold]{stat['hits']}/{stat['k']}[/bold]"
        f"[dim] lowest-health files had a bug fix in the last {window}[/dim]"
    )
    if stat.get("lift") is not None:
        base_pct = round(stat["base_rate"] * 100)
        prec_pct = round(stat["precision"] * 100)
        line += (
            f"[dim], [/dim][bold]{stat['lift']}x[/bold]"
            f"[dim] the {base_pct}% baseline ({prec_pct}% vs {base_pct}%).[/dim]"
        )
    else:
        line += "[dim].[/dim]"
    console.print(line + "\n")


def _effort_bucket(nloc: int) -> tuple[str, int]:
    if nloc <= 40:
        return "S", 1
    if nloc <= 150:
        return "M", 2
    if nloc <= 400:
        return "L", 3
    return "XL", 5


def _suggestion_to_dict(s: object) -> dict:
    """Serialize a ``RefactoringSuggestion`` dataclass to a plain dict."""
    import dataclasses

    return dataclasses.asdict(s) if dataclasses.is_dataclass(s) else dict(s)


def _select_suggestion(suggestions: list, selector: str):
    """Pick one suggestion by a 1-based rank or a target-symbol match.

    ``suggestions`` is the engine's unified-ranked list, so ``"1"`` is the top
    candidate. A non-numeric selector matches ``target_symbol`` exactly first,
    then falls back to a unique case-insensitive substring match.
    """
    if selector.isdigit():
        idx = int(selector) - 1
        if 0 <= idx < len(suggestions):
            return suggestions[idx]
        raise click.ClickException(
            f"Rank {selector} is out of range (1-{len(suggestions)} available)."
        )
    exact = [s for s in suggestions if s.target_symbol == selector]
    if len(exact) == 1:
        return exact[0]
    needle = selector.lower()
    partial = [s for s in suggestions if needle in s.target_symbol.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise click.ClickException(f"No refactoring suggestion matches {selector!r}.")
    names = ", ".join(sorted({s.target_symbol for s in partial})[:8])
    raise click.ClickException(
        f"{selector!r} matches multiple suggestions ({names}). Use a 1-based rank instead."
    )


def _generate_refactoring_code(repo_path, suggestions: list, selector: str, *, fmt: str) -> None:
    """Opt-in LLM code-gen for one suggestion: resolve provider, enrich, render.

    Reuses the repo's configured provider/model (BYO key). The enrichment layer
    is the only place the refactoring feature touches an LLM; it never runs in
    the indexing hot path.
    """
    from repowise.cli.helpers import resolve_provider
    from repowise.core.analysis.health.refactoring.llm import enrich_suggestion

    if not suggestions:
        raise click.ClickException(
            "No refactoring suggestions for this repo. Run `repowise health "
            "--refactoring-targets` first to see what's available."
        )

    suggestion = _select_suggestion(suggestions, selector)

    try:
        provider = resolve_provider(None, None, Path(repo_path))
    except Exception as exc:  # provider misconfig surfaces as a clean CLI error
        raise click.ClickException(
            f"Could not resolve an LLM provider for code generation: {exc}"
        ) from exc

    console.print(
        f"[dim]Generating code for[/dim] [cyan]{suggestion.target_symbol}[/cyan] "
        f"[dim]({suggestion.refactoring_type}) via {getattr(provider, 'model_name', '?')}...[/dim]"
    )
    result = run_async(enrich_suggestion(suggestion, provider=provider, repo_path=Path(repo_path)))

    if fmt == "json":
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    cached = " [dim](cached)[/dim]" if result.cached else ""
    console.print(
        f"\n[bold]{suggestion.refactoring_type}[/bold] · {suggestion.target_symbol} "
        f"[dim]({result.model})[/dim]{cached}"
    )
    if result.validation.get("status") == "checked":
        v = result.validation
        verdict = (
            "[green]improves cohesion[/green]"
            if v.get("improved")
            else "[yellow]no LCOM4 improvement detected[/yellow]"
        )
        console.print(
            f"[dim]Self-check: LCOM4 {v.get('before_lcom4')} → "
            f"max {v.get('after_max_lcom4')} across {v.get('class_count')} classes — {verdict}[/dim]"
        )
    console.print(result.content)


def _render_refactoring_targets(
    metrics: list, findings: list, suggestions: list | None = None, *, fmt: str, limit: int = 20
) -> None:
    """Aggregate findings per file, rank by impact/effort, render.

    When the refactoring layer produced structured *suggestions* (e.g. an
    Extract Class split), the concrete plan is attached to each target's row
    (JSON/MD) and printed as a group tree below the table.
    """
    suggestions = suggestions or []
    sugg_by_file: dict[str, list] = {}
    for s in suggestions:
        sugg_by_file.setdefault(s.file_path, []).append(s)

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
        file_sugg = sugg_by_file.get(path, [])
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
                "plans": [_suggestion_to_dict(s) for s in file_sugg],
            }
        )
    targets.sort(key=lambda t: (-t["impact_per_effort"], -t["total_impact"]))
    targets = targets[:limit]

    # Structured plans are displayed independently of the impact/effort file
    # table (a god class worth splitting may not top that churn-weighted list).
    # The order is the engine's unified rank (impact x centrality x blast
    # radius across all detector types), so we preserve it rather than
    # re-sorting per type.
    ranked_plans = [_suggestion_to_dict(s) for s in suggestions][:limit]

    if fmt == "json":
        click.echo(json.dumps({"targets": targets, "refactoring_plans": ranked_plans}, indent=2))
        return
    if fmt == "md":
        click.echo("# Refactoring targets\n")
        for t in targets:
            click.echo(
                f"- **{t['file_path']}** ({t['effort_bucket']}, "
                f"score {t['score']:.1f}/10, -{t['total_impact']:.2f}) "
                f"— {t['primary_biomarker']}: {t['primary_reason']}"
            )
        _render_extract_class_plans_md(ranked_plans)
        _render_extract_helper_plans_md(ranked_plans)
        _render_extract_method_plans_md(ranked_plans)
        _render_move_method_plans_md(ranked_plans)
        _render_break_cycle_plans_md(ranked_plans)
        _render_split_file_plans_md(ranked_plans)
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
    _render_extract_class_plans_console(ranked_plans)
    _render_extract_helper_plans_console(ranked_plans)
    _render_extract_method_plans_console(ranked_plans)
    _render_move_method_plans_console(ranked_plans)
    _render_break_cycle_plans_console(ranked_plans)
    _render_split_file_plans_console(ranked_plans)


def _render_extract_class_plans_console(plans: list[dict]) -> None:
    """Print the concrete Extract Class splits below the table — the wedge."""
    ec_plans = [p for p in plans if p["refactoring_type"] == "extract_class"]
    if not ec_plans:
        return
    console.print(f"\n[bold]Extract Class plans ({len(ec_plans)})[/bold]")
    for p in ec_plans:
        ev = p["evidence"]
        groups = p["plan"].get("groups", [])
        console.print(
            f"\n[cyan]{p['target_symbol']}[/cyan] [dim]({p['file_path']})[/dim] — "
            f"LCOM4={ev.get('lcom4')}, {ev.get('method_count')} methods, "
            f"WMC={ev.get('wmc')} → split into {len(groups)} classes "
            f"[dim](recover ~{p['impact_delta']:.2f}, effort {p['effort_bucket']}, "
            f"{p['confidence']} confidence)[/dim]"
        )
        for i, g in enumerate(groups, 1):
            fields = ", ".join(g["fields"]) or "—"
            console.print(
                f"  [bold]{i}.[/bold] methods: {', '.join(g['methods'])}\n"
                f"     [dim]fields:[/dim] {fields}"
            )


def _render_extract_class_plans_md(plans: list[dict]) -> None:
    ec_plans = [p for p in plans if p["refactoring_type"] == "extract_class"]
    if not ec_plans:
        return
    click.echo("\n## Extract Class plans\n")
    for p in ec_plans:
        groups = p["plan"].get("groups", [])
        click.echo(
            f"- **{p['target_symbol']}** ({p['file_path']}) — "
            f"LCOM4={p['evidence'].get('lcom4')}, split into {len(groups)} classes:"
        )
        for i, g in enumerate(groups, 1):
            fields = ", ".join(g["fields"]) or "—"
            click.echo(f"  {i}. methods: {', '.join(g['methods'])}  ·  fields: {fields}")


def _render_extract_helper_plans_console(plans: list[dict]) -> None:
    """Print the concrete Extract Helper (clone dedup) plans below the table."""
    eh_plans = [p for p in plans if p["refactoring_type"] == "extract_helper"]
    if not eh_plans:
        return
    console.print(f"\n[bold]Extract Helper plans ({len(eh_plans)})[/bold]")
    for p in eh_plans:
        ev = p["evidence"]
        occ = p["plan"].get("occurrences", [])
        site = p["plan"].get("suggested_site", {}) or {}
        where = site.get("module") or site.get("directory") or "a shared module"
        co = ev.get("co_change_count", 0)
        console.print(
            f"\n[cyan]{ev.get('duplicated_lines')} duplicated lines[/cyan] across "
            f"{len(occ)} sites → extract a helper near [bold]{where}[/bold] "
            f"[dim](recover ~{p['impact_delta']:.2f}, effort {p['effort_bucket']}, "
            f"{p['confidence']} confidence" + (f", co-changed {co}x" if co else "") + ")[/dim]"
        )
        for o in occ:
            console.print(f"  [dim]-[/dim] {o['file']}:{o['line_start']}-{o['line_end']}")


def _render_extract_helper_plans_md(plans: list[dict]) -> None:
    eh_plans = [p for p in plans if p["refactoring_type"] == "extract_helper"]
    if not eh_plans:
        return
    click.echo("\n## Extract Helper plans\n")
    for p in eh_plans:
        ev = p["evidence"]
        occ = p["plan"].get("occurrences", [])
        site = p["plan"].get("suggested_site", {}) or {}
        where = site.get("module") or site.get("directory") or "a shared module"
        click.echo(
            f"- **{ev.get('duplicated_lines')} duplicated lines** across "
            f"{len(occ)} sites — extract a helper near `{where}`:"
        )
        for o in occ:
            click.echo(f"  - {o['file']}:{o['line_start']}-{o['line_end']}")


def _render_extract_method_plans_console(plans: list[dict]) -> None:
    """Print the concrete Extract Method (long-function split) plans below the table."""
    em_plans = [p for p in plans if p["refactoring_type"] == "extract_method"]
    if not em_plans:
        return
    console.print(f"\n[bold]Extract Method plans ({len(em_plans)})[/bold]")
    for p in em_plans:
        pl = p["plan"]
        ev = p["evidence"]
        span = pl.get("span", {}) or {}
        params = ", ".join(pl.get("params", [])) or "—"
        returns = ", ".join(pl.get("returns", [])) or "none"
        console.print(
            f"\n[cyan]{p['target_symbol']}[/cyan] [dim]({p['file_path']})[/dim] — "
            f"extract lines {span.get('start')}-{span.get('end')} "
            f"[dim]({ev.get('slice_nloc')} lines, -{ev.get('ccn_removed')} CCN, "
            f"recover ~{p['impact_delta']:.2f}, effort {p['effort_bucket']}, "
            f"{p['confidence']} confidence)[/dim]"
        )
        console.print(f"  [dim]params (in):[/dim] {params}    [dim]returns (out):[/dim] {returns}")


def _render_extract_method_plans_md(plans: list[dict]) -> None:
    em_plans = [p for p in plans if p["refactoring_type"] == "extract_method"]
    if not em_plans:
        return
    click.echo("\n## Extract Method plans\n")
    for p in em_plans:
        pl = p["plan"]
        ev = p["evidence"]
        span = pl.get("span", {}) or {}
        params = ", ".join(pl.get("params", [])) or "—"
        returns = ", ".join(pl.get("returns", [])) or "none"
        click.echo(
            f"- **{p['target_symbol']}** ({p['file_path']}) — extract lines "
            f"{span.get('start')}-{span.get('end')} ({ev.get('slice_nloc')} lines, "
            f"-{ev.get('ccn_removed')} CCN)  ·  in: {params}  ·  out: {returns}"
        )


def _render_move_method_plans_console(plans: list[dict]) -> None:
    """Print the concrete Move Method (feature-envy) plans below the table."""
    mm_plans = [p for p in plans if p["refactoring_type"] == "move_method"]
    if not mm_plans:
        return
    console.print(f"\n[bold]Move Method plans ({len(mm_plans)})[/bold]")
    for p in mm_plans:
        pl = p["plan"]
        ev = p["evidence"]
        to_file = pl.get("to_file")
        dest = f"{pl.get('to_class')}" + (f" [dim]({to_file})[/dim]" if to_file else "")
        console.print(
            f"\n[cyan]{pl.get('from_class')}.{pl.get('method')}[/cyan] "
            f"[dim]({p['file_path']})[/dim] → move to [bold]{dest}[/bold] "
            f"[dim](uses {ev.get('foreign_calls')} of its members vs "
            f"{ev.get('own_calls')} of its own, effort {p['effort_bucket']}, "
            f"{p['confidence']} confidence)[/dim]"
        )


def _render_move_method_plans_md(plans: list[dict]) -> None:
    mm_plans = [p for p in plans if p["refactoring_type"] == "move_method"]
    if not mm_plans:
        return
    click.echo("\n## Move Method plans\n")
    for p in mm_plans:
        pl = p["plan"]
        ev = p["evidence"]
        dest = pl.get("to_class")
        if pl.get("to_file"):
            dest = f"{dest} ({pl['to_file']})"
        click.echo(
            f"- **{pl.get('from_class')}.{pl.get('method')}** ({p['file_path']}) "
            f"— move to `{dest}` "
            f"(uses {ev.get('foreign_calls')} vs {ev.get('own_calls')} own members)"
        )


def _render_break_cycle_plans_console(plans: list[dict]) -> None:
    """Print the concrete Break Cycle (import-cycle cut) plans below the table."""
    bc_plans = [p for p in plans if p["refactoring_type"] == "break_cycle"]
    if not bc_plans:
        return
    console.print(f"\n[bold]Break Cycle plans ({len(bc_plans)})[/bold]")
    for p in bc_plans:
        pl = p["plan"]
        ev = p["evidence"]
        cuts = pl.get("cut_edges", [])
        console.print(
            f"\n[cyan]Import cycle of {ev.get('cycle_size')} files[/cyan] "
            f"[dim]({ev.get('edge_count')} edges)[/dim] → cut "
            f"{len(cuts)} edge(s) "
            f"[dim](effort {p['effort_bucket']}, {p['confidence']} confidence)[/dim]"
        )
        for e in cuts:
            console.print(f"  [dim]-[/dim] invert {e['from']} → {e['to']}")
        for f in pl.get("cycle", []):
            console.print(f"  [dim]·[/dim] {f}")


def _render_break_cycle_plans_md(plans: list[dict]) -> None:
    bc_plans = [p for p in plans if p["refactoring_type"] == "break_cycle"]
    if not bc_plans:
        return
    click.echo("\n## Break Cycle plans\n")
    for p in bc_plans:
        pl = p["plan"]
        ev = p["evidence"]
        cuts = pl.get("cut_edges", [])
        click.echo(f"- **Import cycle of {ev.get('cycle_size')} files** — cut {len(cuts)} edge(s):")
        for e in cuts:
            click.echo(f"  - invert {e['from']} -> {e['to']}")


def _render_split_file_plans_console(plans: list[dict]) -> None:
    """Print the concrete Split File (module decomposition) plans below the table."""
    sf_plans = [p for p in plans if p["refactoring_type"] == "split_file"]
    if not sf_plans:
        return
    console.print(f"\n[bold]Split File plans ({len(sf_plans)})[/bold]")
    for p in sf_plans:
        pl = p["plan"]
        ev = p["evidence"]
        groups = pl.get("groups", [])
        br = p["blast_radius"]
        shim = " [dim]+shim[/dim]" if pl.get("shim_required") else ""
        console.print(
            f"\n[cyan]{p['file_path']}[/cyan] — "
            f"{ev.get('symbol_count')} symbols, {ev.get('file_nloc')} NLOC, "
            f"modularity {ev.get('modularity')} → split into {len(groups)} files{shim} "
            f"[dim](effort {p['effort_bucket']}, {p['confidence']} confidence, "
            f"{br.get('import_rewrites', 0)} import rewrites in "
            f"{br.get('dependent_count', 0)} files)[/dim]"
        )
        for i, g in enumerate(groups, 1):
            console.print(
                f"  [bold]{i}.[/bold] [green]{g.get('suggested_file')}[/green]: "
                f"{', '.join(g.get('symbols', []))}"
            )
        residual = pl.get("residual")
        if residual and residual.get("symbols"):
            console.print(f"  [dim]core (shared):[/dim] {', '.join(residual['symbols'])}")


def _render_split_file_plans_md(plans: list[dict]) -> None:
    sf_plans = [p for p in plans if p["refactoring_type"] == "split_file"]
    if not sf_plans:
        return
    click.echo("\n## Split File plans\n")
    for p in sf_plans:
        pl = p["plan"]
        ev = p["evidence"]
        groups = pl.get("groups", [])
        click.echo(
            f"- **{p['file_path']}** — {ev.get('symbol_count')} symbols, "
            f"modularity {ev.get('modularity')}, split into {len(groups)} files:"
        )
        for i, g in enumerate(groups, 1):
            click.echo(f"  {i}. `{g.get('suggested_file')}`: {', '.join(g.get('symbols', []))}")
        residual = pl.get("residual")
        if residual and residual.get("symbols"):
            click.echo(f"  - core (shared): {', '.join(residual['symbols'])}")


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
                    per_file_scores={m.file_path: round(float(m.score), 2) for m in metrics},
                )
            except Exception as exc:
                console.print(f"[yellow]Snapshot write skipped: {exc}[/yellow]")

            await session.commit()

    try:
        run_async(_do())
    except Exception as exc:
        console.print(f"[red]Could not persist health to DB: {exc}[/red]")

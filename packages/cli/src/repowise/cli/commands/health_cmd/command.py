"""``repowise health`` Click command + single-repo orchestration.

Reads the health analysis the last ``init``/``update`` stored, the same rows
MCP and the web UI serve, and writes nothing. ``--recompute`` runs the
analysis in-process instead (ingest, analyze, render) and, for a whole-repo
table, writes it back to the index.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from repowise.cli._setup import configure_cli_logging
from repowise.cli.helpers import (
    console,
    err_console,
    load_config,
    load_state,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)
from repowise.core.analysis.health.counts import (
    COUNTS,
    DEFAULT_COUNTS,
    parse_counts,
)
from repowise.core.analysis.health.counts import (
    project as project_counts,
)
from repowise.core.analysis.health.models import split_by_origin
from repowise.core.analysis.health.rows import detail_map, split_unscored
from repowise.core.analysis.health.scope import DEFAULT_SCOPE, SCOPES, parse_scope
from repowise.core.analysis.health.scoring import compute_kpis

from .codegen import _generate_refactoring_code
from .persist import (
    _load_fix_first,
    _load_persisted_coverage_map,
    _load_recommendations,
    _load_stored_report,
    _persist_health,
)
from .refactoring_targets import (
    _render_refactoring_targets,
    _render_stored_refactoring_targets,
)
from .summary import (
    _render_badge,
    _render_defect_accuracy_line,
    _render_distribution_line,
    _render_fix_first,
    _render_performance_section,
    _render_split_line,
)
from .trends import _render_trend

#: Items the report leads with; the full queue is one REST or MCP call away.
FIX_FIRST_ROWS = 3


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
    help=(
        "Print the refactoring queue the index stored, in the order MCP and the "
        "web UI serve it."
    ),
)
@click.option(
    "--recompute",
    is_flag=True,
    default=False,
    help=(
        "Analyze the working tree in-process instead of reading the stored "
        "analysis, and write a whole-repo table run back to the index. Slow on a "
        "large repo; needed outside an indexed one."
    ),
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
    "--scope",
    default=DEFAULT_SCOPE,
    type=click.Choice(list(SCOPES)),
    help=(
        "Which files to report on. Tests score higher than production code, "
        "so 'production' lowers every figure without a defect being found."
    ),
)
@click.option(
    "--counts",
    default=DEFAULT_COUNTS,
    type=click.Choice(list(COUNTS)),
    help=(
        "What the score counts. 'code_shape' removes the git-derived half, "
        "which rises as a file is worked on rather than describing its code."
    ),
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
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def health_command(
    path: str | None,
    file_filter: str | None,
    fmt: str,
    repo_alias: str | None,
    no_workspace: bool,
    refactoring_targets: bool,
    recompute: bool,
    generate_code: str | None,
    module_filter: str | None,
    scope: str,
    counts: str,
    trend_view: bool,
    badge_view: bool,
    verbose: bool,
) -> None:
    """Code-health scores from markers (CCN, nesting, brain-method).

    Reads the analysis the index stored; no LLM, no network, no writes.
    ``--recompute`` re-runs it in-process over the working tree.
    """
    configure_cli_logging(verbose=verbose)

    # Silence structlog/stdlib info+debug lines when the user asked for a
    # machine-readable format so stdout is pure JSON/Markdown and safe to
    # pipe into jq or other tools (e.g. `repowise health --format json | jq .kpis`).
    if fmt != "table":
        silence_logs_for_machine_output_until_close()

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
        # The trend reads stored snapshots, which carry the calibrated score
        # only. Saying so beats printing a projected headline's flag over an
        # unprojected line.
        if parse_scope(scope) != DEFAULT_SCOPE or parse_counts(counts) != DEFAULT_COUNTS:
            status.print(
                "[dim]The trend reads stored snapshots, so --scope and --counts "
                "do not apply to it.[/dim]"
            )
        _render_trend(repo_path, fmt=fmt)
        return

    if refactoring_targets and not recompute and generate_code is None:
        # The stored queue is the calibrated reading, as on MCP and the web UI.
        if parse_scope(scope) != DEFAULT_SCOPE or parse_counts(counts) != DEFAULT_COUNTS:
            status.print(
                "[dim]The stored queue does not take --scope or --counts; pass "
                "--recompute to apply them.[/dim]"
            )
        if not _render_stored_refactoring_targets(
            repo_path, fmt=fmt, file_filter=file_filter, module_filter=module_filter
        ):
            raise click.ClickException(
                "No stored refactoring analysis for this repository. Run `repowise init` "
                "or `repowise update`, or pass --recompute to analyze in-process."
            )
        return

    if recompute or generate_code is not None:
        # Written back only for a whole-repo table: json/md are read by scripts
        # and CI, and a file or module run is an inspection, not repo state.
        report, hotspots, languages = _recompute_report(
            repo_path, persist=fmt == "table" and not file_filter and not module_filter
        )
    else:
        stored = _load_stored_report(repo_path)
        if stored is None:
            raise click.ClickException(
                "No stored health analysis for this repository. Run `repowise init` "
                "or `repowise update`, or pass --recompute to analyze in-process."
            )
        report, hotspots, languages = stored

    metrics = report.metrics
    if file_filter:
        metrics = [m for m in metrics if m.file_path == file_filter]
    if module_filter:
        metrics = [m for m in metrics if m.file_path.startswith(module_filter)]
    narrowed = parse_scope(scope) == "production"
    if narrowed:
        metrics = [m for m in metrics if not m.is_test]
    # Taken before the projection, so a row the projection cannot read still
    # keeps its findings rather than reading as a file that left the repo.
    scoped_paths = {m.file_path for m in metrics}
    # Files in a language health has no dialect for carry no score: they are
    # counted, never ranked or averaged.
    metrics, unanalysed = split_unscored(metrics)
    code_shape = parse_counts(counts) == "code_shape"
    if code_shape:
        # No `unscored` counterpart to the API's: this command scores live, so
        # every row carries the split the projection reads.
        metrics, _ = project_counts(counts, metrics)
    if narrowed or code_shape:
        # Every figure the controls select for. Defect accuracy below is not
        # one of them: it scores the ranking against `prior_defect`, and
        # narrowing leaves it no labels to be accurate about.
        report.kpis = compute_kpis(metrics, hotspots)
    metrics_sorted = sorted(metrics, key=lambda m: m.score)

    findings = report.findings
    if file_filter:
        findings = [f for f in findings if f.file_path == file_filter]
    if module_filter:
        findings = [f for f in findings if f.file_path.startswith(module_filter)]
    if narrowed:
        findings = [f for f in findings if f.file_path in scoped_paths]
    if code_shape:
        # A history finding cannot explain a score the history half was taken
        # out of, so it is not part of this reading.
        findings = split_by_origin(findings)[0]

    if generate_code is not None:
        suggestions = getattr(report, "refactoring_suggestions", None) or []
        if file_filter:
            suggestions = [s for s in suggestions if s.file_path == file_filter]
        if module_filter:
            suggestions = [s for s in suggestions if s.file_path.startswith(module_filter)]
        # Attaches the same batched validation block the read surfaces emit;
        # code generation remains explicitly requested and never auto-applies.
        _load_recommendations(repo_path, suggestions, metrics_sorted)
        _generate_refactoring_code(repo_path, suggestions, generate_code, fmt=fmt)
        return

    if refactoring_targets:
        suggestions = getattr(report, "refactoring_suggestions", None) or []
        if file_filter:
            suggestions = [s for s in suggestions if s.file_path == file_filter]
        if module_filter:
            suggestions = [s for s in suggestions if s.file_path.startswith(module_filter)]
        recommendations = _load_recommendations(repo_path, suggestions, metrics_sorted)
        _render_refactoring_targets(metrics_sorted, findings, recommendations, fmt=fmt)
        return

    if badge_view:
        _render_badge(report.kpis.get("average_health"))
        return

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "kpis": report.kpis,
                    "scope": parse_scope(scope),
                    "counts": parse_counts(counts),
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
                            "details": detail_map(f),
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
        BAND_TERMINAL_COLOR,
        band_for,
    )
    from repowise.core.analysis.health.grading import (
        distribution as health_distribution,
    )

    # Lead with what to fix; a narrowed run is an inspection, not the worklist.
    if not file_filter and not module_filter:
        _render_fix_first(_load_fix_first(repo_path, limit=FIX_FIRST_ROWS))

    kpis = report.kpis
    avg = kpis.get("average_health")
    band_str = ""
    if isinstance(avg, (int, float)):
        band = band_for(float(avg))
        band_color = BAND_TERMINAL_COLOR[band]
        band_str = f" [[{band_color}]{BAND_LABEL[band]}[/{band_color}]]"
    console.print(
        f"\nCode health: [bold]{avg if avg is not None else '?'}[/bold]/10{band_str} · "
        f"Hotspot: [bold]{kpis.get('hotspot_health') or '?'}[/bold]/10 · "
        f"Worst: [bold]{kpis.get('worst_performer_score') or '?'}[/bold]/10 "
        f"({kpis.get('worst_performer_path') or 'n/a'})"
    )
    if code_shape:
        console.print("[dim]Counting code shape only — change history is left out.[/dim]")
    if unanalysed:
        console.print(
            f"[dim]{unanalysed} file(s) not analysed: health does not support "
            "their language yet.[/dim]"
        )
    _render_split_line(kpis)
    _render_distribution_line(health_distribution(metrics))

    _render_defect_accuracy_line(report)

    # Performance pillar section: lead with the finding COUNT + density +
    # coverage (the honest signal), not the bounded /10 average. Metrics carry
    # no language, so it comes with the report from either source.
    _render_performance_section(report, languages)

    table = Table(title=f"Lowest-scoring files ({min(len(metrics_sorted), 20)})")
    table.add_column("File", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("CCN", justify="right")
    table.add_column("Nest", justify="right")
    table.add_column("NLOC", justify="right")
    table.add_column("Test?", justify="center")
    for m in metrics_sorted[:20]:
        score_color = BAND_TERMINAL_COLOR[band_for(m.score)]
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


def _recompute_report(repo_path: Path, *, persist: bool) -> tuple[Any, set[str], dict[str, str]]:
    """Analyze the working tree in-process: ``(report, hotspot paths, language by path)``.

    ``persist`` writes the result to the index, as ``init`` would, so the
    dashboard, MCP tools and ``repowise status`` see the same numbers.
    """
    from repowise.core.analysis.communities import file_community_labels
    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.analysis.health.config import HealthConfig

    graph_builder, parsed_files = _parse_tree(repo_path)
    git_meta_map: dict = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        _, metadata_list = run_async(GitIndexer(repo_path).index_repo(""))
        git_meta_map = {m["file_path"]: m for m in metadata_list}
    except Exception:
        pass

    analyzer = HealthAnalyzer(
        graph_builder.graph(),
        git_meta_map=git_meta_map,
        parsed_files=parsed_files,
        community_label_map=file_community_labels(graph_builder),
        # Coverage folds in from whatever `repowise coverage add` (or index-time
        # ingest) persisted; ingestion lives solely in the `coverage` group.
        coverage_map=_load_persisted_coverage_map(repo_path),
        duplication_cache_dir=Path(repo_path) / ".repowise",
        repo_root=repo_path,
    )
    # Any .repowise/health-rules.json the user keeps in the repo.
    health_cfg = HealthConfig.load(repo_path)
    analyzer_cfg = (
        health_cfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])
        if (health_cfg.disabled_biomarkers or health_cfg.rules)
        else None
    )
    report = analyzer.analyze(analyzer_cfg)
    if persist:
        _persist_health(repo_path, report=report)
    hotspots = {p for p, m in git_meta_map.items() if m.get("is_hotspot")}
    return report, hotspots, {pf.file_info.path: pf.file_info.language for pf in parsed_files}


def _parse_tree(repo_path: Path) -> tuple[Any, list[Any]]:
    """``(graph builder, parsed files)`` over the file set the index analyzed."""
    from repowise.core.ingestion import (
        ASTParser,
        FileTraverser,
        GraphBuilder,
        wire_tsconfig_resolver,
    )

    # A repo initialized with --include-submodules persists the flag in
    # state.json, and a flagless traverser would score a smaller tree.
    state = load_state(repo_path)
    flags = {
        "include_submodules": bool(state.get("include_submodules", False)),
        "include_nested_repos": bool(state.get("include_nested_repos", False)),
    }
    # `--recompute` persists into the rows the indexer writes, so it has to
    # analyze the same file set. Without the config's exclude patterns it
    # scored, and overwrote rows for, files the index had dropped, and on a
    # repo excluding a manifest directory it could write a different `module`.
    exclude_patterns: list[str] = list(load_config(repo_path).get("exclude_patterns") or [])
    traverser = FileTraverser(repo_path, **flags, extra_exclude_patterns=exclude_patterns or None)
    parser = ASTParser()
    graph_builder = GraphBuilder(repo_path, **flags)
    parsed_files = []
    for fi in traverser.traverse():
        try:
            parsed = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
            graph_builder.add_file(parsed)
            parsed_files.append(parsed)
        except Exception:
            continue
    wire_tsconfig_resolver(graph_builder, repo_path, **flags)
    graph_builder.build()
    return graph_builder, parsed_files

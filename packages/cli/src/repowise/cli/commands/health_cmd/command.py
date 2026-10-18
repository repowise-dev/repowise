"""``repowise health`` Click command + single-repo orchestration.

Reads the health analysis the last ``init``/``update`` stored, the same rows
MCP and the web UI serve, and writes nothing. ``--recompute`` runs the
analysis in-process instead (ingest, analyze, render) and, for a whole-repo
table, writes it back to the index.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
from repowise.core.analysis.finding_registry import excluded_types, is_served, preview_lines
from repowise.core.analysis.health.counts import (
    COUNTS,
    DEFAULT_COUNTS,
    parse_counts,
)
from repowise.core.analysis.health.counts import (
    project as project_counts,
)
from repowise.core.analysis.health.models import split_by_origin
from repowise.core.analysis.health.ranking import deduction_by_path
from repowise.core.analysis.health.rows import detail_map, split_unscored
from repowise.core.analysis.health.scope import DEFAULT_SCOPE, SCOPES, parse_scope
from repowise.core.analysis.health.scoring import compute_kpis
from repowise.core.analysis.health.signals import HISTORY_MIN_COMMITS
from repowise.core.persistence.crud.analysis.health import open_finding_count

from .codegen import _generate_refactoring_code
from .persist import (
    _load_fix_first,
    _load_persisted_coverage_map,
    _load_queue_counts,
    _load_recommendations,
    _load_short_history,
    _load_stored_report,
    _persist_health,
)
from .refactoring_targets import (
    _render_plan_recipe,
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
    "--plan",
    "plan_id",
    default=None,
    metavar="ID",
    help=(
        "Print one stored refactoring plan as the recipe an agent applies: its "
        "preconditions, steps and checks (JSON with --format json)."
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
    plan_id: str | None,
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
    opts = _Options(
        fmt=fmt,
        file_filter=file_filter,
        module_filter=module_filter,
        refactoring_targets=refactoring_targets,
        plan_id=plan_id,
        recompute=recompute,
        generate_code=generate_code,
        scope=scope,
        counts=counts,
        trend_view=trend_view,
    )

    # A machine-readable format keeps stdout pure for `--format json | jq`:
    # logs are silenced and status lines go to stderr.
    if fmt != "table":
        silence_logs_for_machine_output_until_close()
    status = err_console if fmt != "table" else console

    repo_path = _resolve_repo_path(path, no_workspace, repo_alias, status)
    status.print(f"[bold]repowise health[/bold] — {repo_path}")
    if _render_stored_view(repo_path, status, opts):
        return

    report, hotspots, languages = _load_report(repo_path, opts)
    metrics, scored, unanalysed = _scope_report(report, hotspots, opts)
    metrics_sorted = sorted(metrics, key=lambda m: m.score)
    # JSON explains the score with every finding it counted; the rest list
    # what MCP and the web UI list.
    excluded = excluded_types()
    findings = [f for f in scored if not _withheld(f, excluded)]

    if generate_code is not None:
        suggestions = _scoped_suggestions(report, opts)
        # Attaches the same batched validation block the read surfaces emit;
        # code generation remains explicitly requested and never auto-applies.
        _load_recommendations(repo_path, suggestions, metrics_sorted, findings)
        _generate_refactoring_code(repo_path, suggestions, generate_code, fmt=fmt)
    elif refactoring_targets:
        suggestions = _scoped_suggestions(report, opts)
        recommendations = _load_recommendations(repo_path, suggestions, metrics_sorted, findings)
        _render_refactoring_targets(metrics_sorted, findings, recommendations, fmt=fmt)
    elif badge_view:
        _render_badge(report.kpis.get("average_health"), report.kpis.get("analysed_share"))
    elif fmt == "json":
        _render_json(repo_path, report, metrics_sorted, scored, opts)
    elif fmt == "md":
        _render_md(report, findings)
    else:
        _render_table(
            repo_path, report, (metrics, metrics_sorted), findings, unanalysed, languages, opts
        )


def _withheld(finding: Any, excluded: frozenset[str]) -> bool:
    """A finding the registry or a language gate keeps off default surfaces."""
    return not is_served("health", finding.biomarker_type, finding.file_path, excluded)


@dataclass(frozen=True)
class _Options:
    """The options that pick a view and narrow the report."""

    fmt: str
    file_filter: str | None
    module_filter: str | None
    refactoring_targets: bool
    plan_id: str | None
    recompute: bool
    generate_code: str | None
    scope: str
    counts: str
    trend_view: bool

    @property
    def controls_set(self) -> bool:
        """``--scope`` or ``--counts`` asks for other than the stored reading."""
        return (
            parse_scope(self.scope) != DEFAULT_SCOPE or parse_counts(self.counts) != DEFAULT_COUNTS
        )

    def keeps(self, file_path: str) -> bool:
        """Whether ``--file`` and ``--module`` keep a row on *file_path*."""
        if self.file_filter and file_path != self.file_filter:
            return False
        return not (self.module_filter and not file_path.startswith(self.module_filter))


def _resolve_repo_path(
    path: str | None, no_workspace: bool, repo_alias: str | None, status: Any
) -> Path:
    """The repository to report on: the named or primary workspace repo, else *path*'s."""
    target = resolve_command_target(
        path=path, no_workspace_flag=no_workspace, repo_alias=repo_alias
    )
    target.notice(status, command="health")
    if not target.is_workspace:
        assert target.repo_path is not None
        return target.repo_path
    if target.repo_filter is not None:
        picked = target.resolve_repo_alias(target.repo_filter)
        if picked is None:
            raise click.ClickException(f"Unknown repo alias: {target.repo_filter}")
        return picked
    primary = target.primary_path()
    if primary is None:
        raise click.ClickException("Workspace has no primary repo configured.")
    return primary


def _render_stored_view(repo_path: Path, status: Any, opts: _Options) -> bool:
    """The trend, one plan or the stored refactoring queue; ``False`` when none was asked for."""
    if opts.trend_view:
        # Stored snapshots carry the calibrated score only: say so rather than
        # print a projected headline's flag over an unprojected line.
        if opts.controls_set:
            status.print(
                "[dim]The trend reads stored snapshots, so --scope and --counts "
                "do not apply to it.[/dim]"
            )
        _render_trend(repo_path, fmt=opts.fmt)
        return True
    if opts.plan_id is not None:
        if not _render_plan_recipe(repo_path, opts.plan_id, fmt=opts.fmt):
            raise click.ClickException(
                f"No stored refactoring plan {opts.plan_id}. "
                "`repowise health --refactoring-targets` lists the current plans."
            )
        return True
    if opts.refactoring_targets and not opts.recompute and opts.generate_code is None:
        # The stored queue is the calibrated reading, as on MCP and the web UI.
        if opts.controls_set:
            status.print(
                "[dim]The stored queue does not take --scope or --counts; pass "
                "--recompute to apply them.[/dim]"
            )
        if not _render_stored_refactoring_targets(
            repo_path, fmt=opts.fmt, file_filter=opts.file_filter, module_filter=opts.module_filter
        ):
            raise click.ClickException(
                "No stored refactoring analysis for this repository. Run `repowise init` "
                "or `repowise update`, or pass --recompute to analyze in-process."
            )
        return True
    return False


def _load_report(repo_path: Path, opts: _Options) -> tuple[Any, set[str], dict[str, str]]:
    """``(report, hotspot paths, language by path)``, recomputed or as stored."""
    if opts.recompute or opts.generate_code is not None:
        # Written back only for a whole-repo table: json/md are read by scripts
        # and CI, and a file or module run is an inspection, not repo state.
        return _recompute_report(
            repo_path,
            persist=opts.fmt == "table" and not opts.file_filter and not opts.module_filter,
        )
    stored = _load_stored_report(repo_path)
    if stored is None:
        raise click.ClickException(
            "No stored health analysis for this repository. Run `repowise init` "
            "or `repowise update`, or pass --recompute to analyze in-process."
        )
    return stored


def _scope_report(
    report: Any, hotspots: set[str], opts: _Options
) -> tuple[list[Any], list[Any], int]:
    """``(metrics, findings, unanalysed)`` narrowed by the filters, ``--scope`` and ``--counts``.

    When ``--scope`` or ``--counts`` changes the reading, ``report.kpis`` is
    recomputed over the narrowed metrics.
    """
    metrics = [m for m in report.metrics if opts.keeps(m.file_path)]
    narrowed = parse_scope(opts.scope) == "production"
    if narrowed:
        metrics = [m for m in metrics if not m.is_test]
    # Taken before the projection, so a row the projection cannot read still
    # keeps its findings rather than reading as a file that left the repo.
    scoped_paths = {m.file_path for m in metrics}
    # Files in a language health has no dialect for carry no score: they are
    # counted, never ranked or averaged.
    metrics, unanalysed = split_unscored(metrics)
    code_shape = parse_counts(opts.counts) == "code_shape"
    if code_shape:
        # No `unscored` counterpart to the API's: this command scores live, so
        # every row carries the split the projection reads.
        metrics, _ = project_counts(opts.counts, metrics)
    if narrowed or code_shape:
        # Defect accuracy is not recomputed: it scores the ranking against
        # `prior_defect`, and narrowing leaves it no labels.
        report.kpis = compute_kpis(
            metrics,
            hotspots,
            unanalysed=unanalysed,
            deductions=deduction_by_path(report.findings),
        )

    findings = _scope_findings(report.findings, opts, scoped_paths if narrowed else None)
    return metrics, findings, unanalysed


def _scope_findings(
    findings: list[Any], opts: _Options, scoped_paths: set[str] | None
) -> list[Any]:
    """*findings* the filters keep, on *scoped_paths* when ``--scope`` narrowed the files."""
    findings = [f for f in findings if opts.keeps(f.file_path)]
    if scoped_paths is not None:
        findings = [f for f in findings if f.file_path in scoped_paths]
    if parse_counts(opts.counts) == "code_shape":
        # A history finding cannot explain a score the history half was taken
        # out of, so it is not part of this reading.
        findings = split_by_origin(findings)[0]
    return findings


def _scoped_suggestions(report: Any, opts: _Options) -> list[Any]:
    suggestions = getattr(report, "refactoring_suggestions", None) or []
    return [s for s in suggestions if opts.keeps(s.file_path)]


def _render_json(
    repo_path: Path, report: Any, metrics_sorted: list[Any], findings: list[Any], opts: _Options
) -> None:
    excluded = excluded_types()
    short_history = _load_short_history(repo_path)
    click.echo(
        json.dumps(
            {
                "kpis": report.kpis,
                "scope": parse_scope(opts.scope),
                "counts": parse_counts(opts.counts),
                # Findings, causes, plans and Fix first items, as every
                # surface counts them; ``shown`` is 0, nothing is listed.
                "queue_counts": _load_queue_counts(repo_path),
                "metrics": [_json_metric(m, short_history) for m in metrics_sorted],
                "findings": [
                    {
                        "biomarker_type": f.biomarker_type,
                        "severity": str(f.severity),
                        "file_path": f.file_path,
                        "function_name": f.function_name,
                        "health_impact": f.health_impact,
                        "details": detail_map(f),
                        "reason": f.reason,
                        **({"withheld": True} if _withheld(f, excluded) else {}),
                    }
                    for f in findings
                ],
            },
            indent=2,
        )
    )


def _json_metric(m: Any, short_history: set[str]) -> dict[str, Any]:
    return {
        "file_path": m.file_path,
        "score": m.score,
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "branch_coverage_pct": m.branch_coverage_pct,
        "duplication_pct": m.duplication_pct,
        **({"history": "short"} if m.file_path in short_history else {}),
    }


def _render_md(report: Any, findings: list[Any]) -> None:
    click.echo("# Code Health Report\n")
    for k, v in report.kpis.items():
        click.echo(f"- **{k}**: {v}")
    click.echo("\n## Findings\n")
    for f in findings:
        click.echo(
            f"- [{f.severity}] `{f.file_path}` {f.function_name or ''} "
            f"- {f.reason} (impact -{f.health_impact:.2f})"
        )


def _render_table(
    repo_path: Path,
    report: Any,
    metrics: tuple[list[Any], list[Any]],
    findings: list[Any],
    unanalysed: int,
    languages: dict[str, str],
    opts: _Options,
) -> None:
    """The terminal report. *metrics* is ``(as scoped, sorted by score)``."""
    from repowise.core.analysis.health.grading import distribution as health_distribution

    scoped, metrics_sorted = metrics
    # Lead with what to fix; a narrowed run is an inspection, not the worklist.
    if not opts.file_filter and not opts.module_filter:
        _render_fix_first(_load_fix_first(repo_path, limit=FIX_FIRST_ROWS))
    _render_headline(report.kpis, parse_counts(opts.counts) == "code_shape", unanalysed)
    _render_split_line(report.kpis)
    _render_distribution_line(health_distribution(scoped))
    _render_defect_accuracy_line(report)
    # Lead with the finding count, density and coverage, not the bounded /10
    # average. Metrics carry no language, so it comes with the report.
    _render_performance_section(report, findings, languages)
    _render_files_table(metrics_sorted, _load_short_history(repo_path))
    _render_findings_table(findings)


def _render_headline(kpis: dict[str, Any], code_shape: bool, unanalysed: int) -> None:
    from repowise.core.analysis.health.grading import (
        BAND_LABEL,
        BAND_TERMINAL_COLOR,
        analysed_note,
        headline_band,
    )

    avg = kpis.get("average_health")
    band_str = ""
    band = headline_band(avg, kpis.get("analysed_share"))
    if band is not None:
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
    # The share behind the headline, from the same counts as its band.
    note = analysed_note(kpis.get("file_count") or 0, kpis.get("unanalysed_file_count") or 0)
    if note:
        console.print(f"[dim]Code health {note}.[/dim]")


def _render_files_table(metrics_sorted: list[Any], short_history: set[str]) -> None:
    from repowise.core.analysis.health.grading import BAND_TERMINAL_COLOR, band_for

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
            f"[{score_color}]{m.score:.1f}[/{score_color}]"
            + ("*" if m.file_path in short_history else ""),
            str(m.max_ccn),
            str(m.max_nesting),
            str(m.nloc),
            "✓" if m.has_test_file else "—",
        )
    console.print(table)
    if any(m.file_path in short_history for m in metrics_sorted[:20]):
        console.print(
            f"[dim]* Short history (under {HISTORY_MIN_COMMITS} commits): most history "
            "markers need more, so the score is mostly code shape.[/dim]"
        )


def _render_findings_table(findings: list[Any]) -> None:
    if not findings:
        return
    # The total get_overview prints; advisory rows are listed, not counted.
    console.print(f"\n[bold]{open_finding_count(findings)}[/bold] open findings:")
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
    for line in preview_lines("health", findings[:30], kind="biomarker_type"):
        console.print(f"[dim]{line}[/dim]")


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
    """``(graph builder, parsed files)`` over the file set the index analyzed.

    The graph is the one ``update`` builds (framework, pytest-root and
    dynamic-hint edges included), since ``--recompute`` persists into the
    rows the indexer writes. ``read_only`` keeps the parse cache, centrality
    cache and structural episodes untouched.
    """
    from repowise.core.pipeline.incremental import build_repo_graph

    # A repo initialized with --include-submodules persists the flag in
    # state.json, and a flagless traverser would score a smaller tree.
    state = load_state(repo_path)
    # Without the config's exclude patterns it scored, and overwrote rows for,
    # files the index had dropped, and on a repo excluding a manifest
    # directory it could write a different `module`.
    parsed_files, _sources, graph_builder, _structure, _count = build_repo_graph(
        repo_path,
        list(load_config(repo_path).get("exclude_patterns") or []),
        collect_sources=True,
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
        read_only=True,
    )
    return graph_builder, parsed_files

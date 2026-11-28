"""``repowise dead-code`` — detect dead and unused code."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    cross_repo_cut_inputs,
    get_head_commit,
    load_config,
    load_state,
    repo_index_session,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)
from repowise.cli.output import notice_console
from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeReport
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
from repowise.core.analysis.dead_code.serving import (
    CrossRepoLookup,
    ServedFinding,
    is_safe,
    safe_only_note,
    serve_findings,
)
from repowise.core.analysis.finding_registry import preview_lines

#: The floor ``init`` and ``update`` store findings at: they run the analyzer
#: with its default config, which keeps nothing below this and runs all four
#: detectors. A request inside that config is answered from the store.
_STORED_FLOOR = RISK_CAP_CONFIDENCE

#: Finding kind to the config switch that detects it.
_KIND_SWITCH = {
    "unreachable_file": "detect_unreachable_files",
    "unused_export": "detect_unused_exports",
    "unused_internal": "detect_unused_internals",
    "zombie_package": "detect_zombie_packages",
}


def _live_reason(repo_path: Path, min_confidence: float) -> str | None:
    """Why the stored findings cannot answer this request, or ``None`` if they can."""
    if min_confidence < _STORED_FLOOR:
        return (
            f"--min-confidence {min_confidence:g} is below the {_STORED_FLOOR:g} "
            "floor the index stored"
        )
    head = get_head_commit(repo_path)
    indexed = load_state(repo_path).get("last_sync_commit")
    if head and indexed and head != indexed:
        return (
            f"the index is at {(indexed or 'no commit')[:7]} but HEAD is {head[:7]}; "
            "run 'repowise update' to make this instant"
        )
    return None


async def _read_stored(repo_path: Path, config: dict) -> DeadCodeReport | None:
    """The findings the last ``init``/``update`` stored, or ``None`` with no readable index.

    Deliberate shortcut: an index whose dead-code pass failed stores no rows
    and reads as a clean repo here, as it already does for ``get_dead_code``.
    Telling the two apart needs the pass to record that it ran.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence.crud import finding_data_from_row, get_dead_code_findings

    async with repo_index_session(repo_path) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        try:
            # Withheld kinds included: this command never applied the registry,
            # and ``--include-internals`` asks for the hidden kind by name.
            rows = await get_dead_code_findings(session, repo_id, include_withheld=True)
        except (SQLAlchemyError, OSError):
            return None
    wanted = {kind for kind, switch in _KIND_SWITCH.items() if config[switch]}
    findings = [finding_data_from_row(r) for r in rows if r.kind in wanted]
    shown = [f for f in findings if f.confidence >= config["min_confidence"]]
    return DeadCodeReport.from_findings(
        shown, hidden_below_threshold=len(findings) - len(shown)
    )


def _notice_stored(repo_path: Path, notices: Any) -> None:
    from repowise.core.analysis.change_risk import working_tree_is_dirty

    notices.print(
        f"[dim]Read from the index. Findings below {_STORED_FLOOR:g} are not stored; "
        "--min-confidence 0.0 computes them live.[/dim]"
    )
    if working_tree_is_dirty(str(repo_path)):
        notices.print(
            "[yellow]The working tree has changes the index does not include; "
            "run 'repowise update' after committing to refresh these findings.[/yellow]"
        )


def _finding_order(f: DeadCodeFindingData) -> tuple:
    """Highest confidence first, then a stable order, whichever path produced it."""
    return (-f.confidence, f.kind.value, f.file_path, f.start_line or 0, f.symbol_name or "")


def _analyze_live(repo_path: Path, config: dict, notices: Any) -> DeadCodeReport:
    """Parse the working tree and run the analyzer over it.

    The graph is the one ``update`` builds (same file set, framework and
    dynamic-hint edges), so a live answer differs from the stored one only by
    the code that changed. ``read_only`` keeps this command from writing into
    the index.
    """
    from repowise.core.analysis.dead_code import DeadCodeAnalyzer
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.pipeline.incremental import build_repo_graph

    state = load_state(repo_path)
    _parsed, source_map, graph_builder, _structure, _count = build_repo_graph(
        repo_path,
        list(load_config(repo_path).get("exclude_patterns") or []),
        collect_sources=True,
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
        log=notices.print,
        read_only=True,
    )

    # Git metadata (best effort)
    git_meta_map: dict = {}
    try:
        _, metadata_list = run_async(GitIndexer(repo_path, read_only=True).index_repo(""))
        git_meta_map = {m["file_path"]: m for m in metadata_list}
    except Exception:
        pass

    # parsed_files powers the source-scan rescues (dynamic-import markers,
    # bundler resolve.alias targets, export-alias maps).
    analyzer = DeadCodeAnalyzer(
        graph_builder.graph(),
        git_meta_map,
        parsed_files=graph_builder._parsed_files,
        source_map=source_map,
        repo_root=repo_path,
        unindexed_source_files=graph_builder.traversal_stats.unindexed_source_files(),
        dotnet_index=getattr(graph_builder, "dotnet_index", None),
    )
    return analyzer.analyze(config)


def _with_cut(served: ServedFinding) -> DeadCodeFindingData:
    if served.cross_repo_note is None:
        return served.row
    return replace(
        served.row,
        confidence=served.confidence,
        safe_to_delete=is_safe(served),
        evidence=[*served.row.evidence, served.cross_repo_note],
    )


def _cut_cross_repo(
    report: DeadCodeReport, lookup: CrossRepoLookup | None, alias: str | None, floor: float
) -> tuple[DeadCodeReport, int]:
    """The cross-repo cut every surface applies, then the floor again, since it only lowers.

    Returns the report and how many findings the cut touched.
    """
    if lookup is None:
        return report, 0
    served = serve_findings(report.findings, lookup, alias)
    cut = [_with_cut(s) for s in served]
    shown = [f for f in cut if f.confidence >= floor]
    touched = sum(1 for s in served if s.cross_repo_note)
    return DeadCodeReport.from_findings(
        shown, hidden_below_threshold=report.hidden_below_threshold + len(cut) - len(shown)
    ), touched


@click.command("dead-code")
@click.argument("path", required=False, type=click.Path(exists=True))
@click.option(
    "--min-confidence",
    default=RISK_CAP_CONFIDENCE,
    type=float,
    help="Minimum confidence threshold.",
)
@click.option(
    "--safe-only",
    is_flag=True,
    help="Only show deletion-ready findings (high confidence, no runtime-load risk factors).",
)
@click.option(
    "--kind",
    type=click.Choice(["unreachable_file", "unused_export", "unused_internal", "zombie_package"]),
    help="Filter by finding kind.",
)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json", "md"]),
    help="Output format.",
)
@click.option(
    "--include-internals/--no-include-internals",
    default=False,
    help="Detect unused private symbols (higher false-positive rate, off by default).",
)
@click.option(
    "--include-zombie-packages/--no-include-zombie-packages",
    default=True,
    help="Detect monorepo packages with no external importers (on by default).",
)
@click.option(
    "--no-unreachable",
    "no_unreachable",
    is_flag=True,
    default=False,
    help="Skip detection of unreachable files (in_degree=0).",
)
@click.option(
    "--no-unused-exports",
    "no_unused_exports",
    is_flag=True,
    default=False,
    help="Skip detection of unused public exports.",
)
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Workspace repo alias to analyze (implies workspace mode).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the live analysis (table format only).",
)
def dead_code_command(
    path: str | None,
    min_confidence: float,
    safe_only: bool,
    kind: str | None,
    fmt: str,
    include_internals: bool,
    include_zombie_packages: bool,
    no_unreachable: bool,
    no_unused_exports: bool,
    repo_alias: str | None,
    no_workspace: bool,
    verbose: bool,
) -> None:
    """Detect dead and unused code.

    Reads the findings the last ``init``/``update`` stored when the index is
    at HEAD and the request is inside what that run computed; otherwise, or
    with no index, it parses the working tree and analyzes it live.

    In workspace mode, analyzes the primary repo by default; pass
    --repo <alias> to target a different repo. A finding whose file another
    workspace repo may use is never cleanup-ready, with the same confidence as
    on every other surface. Run once per repo.
    """
    # The live path parses the whole tree, and its structlog lines go to
    # stdout: they would corrupt JSON/Markdown and bury the table.
    if fmt != "table" or not verbose:
        silence_logs_for_machine_output_until_close()

    target = resolve_command_target(
        path=path,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    notices = notice_console(fmt)
    repo_path = target.narrow(notices, command="dead-code")
    notices.print(f"[bold]repowise dead-code[/bold]: {repo_path}")

    config: dict = {
        "min_confidence": min_confidence,
        "detect_unused_internals": include_internals,
        "detect_zombie_packages": include_zombie_packages,
        "detect_unreachable_files": not no_unreachable,
        "detect_unused_exports": not no_unused_exports,
    }
    if kind:
        # --kind overrides the individual detection flags to focus on one type
        config["detect_unreachable_files"] = kind == "unreachable_file"
        config["detect_unused_exports"] = kind == "unused_export"
        config["detect_unused_internals"] = kind == "unused_internal"
        config["detect_zombie_packages"] = kind == "zombie_package"

    reason = _live_reason(repo_path, min_confidence)
    report = None if reason else run_async(_read_stored(repo_path, config))
    if report is None:
        if reason:
            notices.print(f"[dim]Computing live: {reason}.[/dim]")
        report = _analyze_live(repo_path, config, notices)
    else:
        _notice_stored(repo_path, notices)
    cut = cross_repo_cut_inputs(repo_path, None if no_workspace else target.ws_root)
    report, touched = _cut_cross_repo(report, cut["lookup"], cut["alias"], min_confidence)
    if touched:
        notices.print(
            f"[dim]{touched} finding(s) not cleanup-ready: another workspace repo may use "
            "their file.[/dim]"
        )
    findings = sorted(report.findings, key=_finding_order)

    if safe_only:
        safe = [f for f in findings if f.safe_to_delete]
        if note := safe_only_note(len(findings), len(safe)):
            notices.print(f"[dim]{note}[/dim]")
        findings = safe

    if fmt == "json":
        output = []
        for f in findings:
            output.append(
                {
                    "kind": f.kind.value,
                    "file_path": f.file_path,
                    "symbol_name": f.symbol_name,
                    "confidence": f.confidence,
                    "reason": f.reason,
                    "safe_to_delete": f.safe_to_delete,
                    "risk_factors": f.risk_factors,
                    "lines": f.lines,
                    "primary_owner": f.primary_owner,
                    "export_only": f.export_only,
                }
            )
        click.echo(json.dumps(output, indent=2))
        return

    if fmt == "md":
        click.echo("# Dead Code Report\n")
        click.echo(f"**Total findings:** {len(findings)}")
        click.echo(f"**Cleanup-candidate lines:** {report.deletable_lines}\n")
        for f in findings:
            safe = " (cleanup-ready)" if f.safe_to_delete else ""
            name = f"`{f.symbol_name}`" if f.symbol_name else f"`{f.file_path}`"
            click.echo(f"- [{f.kind.value}] {name}: {f.reason} ({f.confidence:.0%}){safe}")
        if report.hidden_below_threshold:
            click.echo(
                f"\n> {report.hidden_below_threshold} finding(s) hidden below threshold "
                f"(confidence < {min_confidence:.2g}); "
                f"pass `--min-confidence 0.0` to see them."
            )
        return

    # Table format (default)
    table = Table(title=f"Dead Code ({len(findings)} findings)")
    table.add_column("Kind", style="cyan")
    table.add_column("File / Symbol")
    table.add_column("Confidence", justify="right")
    table.add_column("Ready?", justify="center")
    table.add_column("Lines", justify="right")
    table.add_column("Reason")

    for f in findings:
        name = f.symbol_name or f.file_path
        safe = "[green]✓[/green]" if f.safe_to_delete else "[red]✗[/red]"
        table.add_row(
            f.kind.value,
            name,
            f"{f.confidence:.0%}",
            safe,
            "—" if f.lines is None else str(f.lines),
            f.reason[:60],
        )

    console.print(table)
    for line in preview_lines(
        "dead_code",
        findings,
        kind="kind",
        get=lambda f, k: f.kind.value if k == "kind" else getattr(f, k),
    ):
        console.print(f"[dim]{line}[/dim]")
    # confidence_summary is a {"high": N, ...} dict; interpolating it printed a
    # raw Python repr, braces and quotes included, at the end of an otherwise
    # formatted report.
    tiers = ", ".join(
        f"{tier} {count}"
        for tier in ("high", "medium", "low")
        if (count := report.confidence_summary.get(tier, 0))
    )
    console.print(
        f"\nCleanup-candidate lines: [bold]{report.deletable_lines:,}[/bold]"
        + (f" · findings by confidence: {tiers}" if tiers else "")
    )
    if report.hidden_below_threshold:
        console.print(
            f"[dim]{report.hidden_below_threshold} finding(s) hidden below "
            f"threshold (confidence < {min_confidence:.2g}); "
            f"pass --min-confidence 0.0 to see them.[/dim]"
        )

"""Refactoring-target aggregation and per-refactoring-type plan renderers.

Ranks findings per file by impact/effort, then prints the concrete
structured plans (Extract Class/Helper/Method, Move Method, Break Cycle,
Split File) for both console and Markdown output.
"""

from __future__ import annotations

import json

import click
from rich.table import Table

from repowise.cli.helpers import console


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
    table.add_column("Primary marker")
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

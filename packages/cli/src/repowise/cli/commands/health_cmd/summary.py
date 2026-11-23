"""Console renderers for the top-of-report summary lines.

The Fix-first lead, performance-risk headline, band distribution, README
badge, and the "does the score find the bugs?" defect-accuracy line.
"""

from __future__ import annotations

from typing import Any

from repowise.cli.helpers import console


def _render_fix_first(loaded: tuple[Any, Any] | None) -> None:
    """What to fix first, as core ranks it: title, where, why, effort, how to
    verify. ``loaded`` is the queue's head and the items' stored counts."""
    from rich.markup import escape

    if loaded is None:
        return
    queue, counts = loaded
    if not queue.items:
        console.print("\n[bold]Fix first[/bold]: nothing eligible in the stored analysis.")
        return
    shown = queue.items
    console.print(
        f"\n[bold]Fix first[/bold] [dim]({counts.shown} of {counts.eligible:,} eligible items, "
        f"{counts.due:,} due; {counts.inventory:,} in the inventory, tests, tooling and "
        "history-only files left out)[/dim]"
    )
    for item in shown:
        target = item.target
        where = target.file_path + (f":{target.line_start}" if target.line_start else "")
        console.print(
            f" {item.rank + 1}. [bold]{escape(item.title)}[/bold] "
            f"[dim]{item.tier} · effort {item.effort.bucket} · {item.gain.text}[/dim]"
        )
        console.print(f"    [cyan]{escape(where)}[/cyan]")
        console.print(f"    {escape(item.why)}")
        if item.verify.command:
            console.print(f"    [dim]verify:[/dim] {escape(item.verify.command)}")


def _render_performance_section(report: Any, coverage: Any, causes: Any | None) -> None:
    """Honest performance headline: the open causes + coverage + scope.

    Leads with the causes worth fixing now (``crud.performance_headline``, as
    every surface counts them) and how much of the analyzed code a perf
    detector actually ran on, so a mostly-unsupported-language repo reads a low
    coverage % rather than a meaningless bounded 10/10. Silent when no code file
    carries a supported language (nothing to say).
    """
    if not coverage.analyzed_files:
        return
    perf_avg = report.kpis.get("performance_average")

    parts = []
    if causes is not None:
        parts.append(
            f"[bold]{causes.now:,}[/bold] cause{'s' if causes.now != 1 else ''} worth "
            f"fixing now · {causes.production:,} open in production code · {causes.all:,} overall"
        )
        if causes.per_10k_loc is not None:
            parts.append(f"{causes.per_10k_loc}/10K covered LOC")
    if isinstance(perf_avg, (int, float)):
        parts.append(f"avg score {perf_avg:.1f}/10")
    console.print(
        "\n[bold]Performance[/bold] "
        "[dim](static, high-precision/low-recall)[/dim]: " + " · ".join(parts)
    )

    cov_line = (
        f"[dim]Coverage:[/dim] perf ran on {coverage.pct_loc}% of analyzed code lines "
        f"({coverage.covered_files}/{coverage.analyzed_files} files)"
    )
    if coverage.skipped_files:
        langs = ", ".join(f"{lang} x{n}" for lang, n in coverage.unsupported_languages[:3])
        cov_line += f"; {coverage.skipped_files} skipped in unsupported languages ({langs})"
    console.print(cov_line)
    console.print(
        "[dim]Scope: I/O-in-loop (N+1 on database calls), resource/regex/defer-in-loop, "
        "blocking-in-async. "
        "Not covered: algorithmic blowups, GC pressure, ORM lazy-load N+1.[/dim]"
    )


_MEASURED_GROUPS = (("measured", "Measured"), ("measured_in_tests", "Measured in tests"))


def _render_measured_costs(view: dict[str, Any] | None) -> None:
    """What each workload's newest run measured (``crud.measured_costs``), one
    block per run.

    Each block opens with its basis and where the run's time went, so a ranked
    function is never read without its workload. Rows lead with the function's
    self time, the cost it is ranked by. Silent without ranked evidence.
    """
    from rich.markup import escape

    if not view:
        return
    for key, title in _MEASURED_GROUPS:
        group = view.get(key)
        if not group:
            continue
        for run in group["runs"]:
            console.print(f"\n[bold]{title}[/bold] [dim]({escape(_run_basis(run))})[/dim]")
            rows = [r for r in group["rows"] if r["run_id"] == run["run_id"]]
            for row in rows:
                console.print(f"  {row['rank']}. [cyan]{escape(row['symbol_id'])}[/cyan]")
                console.print(f"     [dim]{escape(_row_facts(row))}[/dim]")


def _render_connect_line(freshness: dict[str, Any] | None) -> None:
    """One line pointing at ``evidence setup`` when no runtime evidence is
    stored, so the performance figures above read as code shape. Silent when
    the store cannot say."""
    from repowise.core.evidence.setup import EVIDENCE_SETUP

    if not freshness or freshness["runtime"]["present"]:
        return
    console.print(
        "\n[dim]No runtime evidence stored, so performance here is code shape only. "
        f"Connect a real run: [bold]{EVIDENCE_SETUP}[/bold] prints the profile and "
        "ingest commands.[/dim]"
    )


def _pct(value: float | None) -> str:
    return "?" if value is None else f"{value * 100:.0f}%"


def _run_basis(run: dict[str, Any]) -> str:
    """``entry_point "api" at abc1234, 3 days old: repo code itself 47%, waits 30%,
    libraries 12% of 5,300 samples``."""
    from repowise.core.evidence.status import split_words

    parts = [f'{run["workload_class"]} "{run["workload_name"]}"']
    if run.get("commit"):
        parts.append(f"at {run['commit'][:7]}")
    if run.get("age_days") is not None:
        parts.append(f"{run['age_days']} day{'s' if run['age_days'] != 1 else ''} old")
    basis = ", ".join(parts)
    samples = run.get("samples")
    split = split_words(run)
    if split is None:
        where = "time split unknown (stored before it was recorded; add the profile again)"
    else:
        where = split + (f" of {samples:,} samples" if samples else " of the run")
    if run.get("ordering_only"):
        where += "; traced, so the order counts and no share is given"
    return f"{basis}: {where}"


def _row_facts(row: dict[str, Any]) -> str:
    parts = []
    if row.get("share") is not None:
        interval = ""
        if row.get("share_low") is not None and row.get("share_high") is not None:
            interval = f" ({_pct(row['share_low'])} to {_pct(row['share_high'])})"
        parts.append(f"self {_pct(row['share'])} of the run{interval}")
    if row.get("inclusive") is not None:
        parts.append(f"with callees {_pct(row['inclusive'])}")
    if row.get("samples") is not None:
        parts.append(f"{row['samples']:,} samples")
    callers = row.get("callers_total")
    if callers is not None:
        parts.append(f"{callers} caller{'s' if callers != 1 else ''}")
    if row.get("owner"):
        parts.append(f"owner {row['owner']['name']}")
    tests = row.get("tests")
    if tests is None:
        parts.append("tests not mapped")
    elif tests:
        parts.append(f"nearest test {tests[0]}")
    else:
        parts.append("no test reaches it")
    if row.get("on_static_findings"):
        parts.append(f"static cause {', '.join(row['on_static_findings'])}")
    return " · ".join(parts)


def _render_split_line(kpis: dict) -> None:
    """The headline's two halves, so a reader can see which one holds it down."""
    structure = kpis.get("structure_average")
    history = kpis.get("history_average")
    if structure is None or history is None:
        return
    console.print(
        f"[dim]Of that deduction, [/dim]{structure:.2f}[dim] is code shape and [/dim]"
        f"{history:.2f}[dim] is history. History answers to time, not to editing.[/dim]"
    )


def _render_distribution_line(dist: dict) -> None:
    """One compact line: the NLOC-weighted file split across the bands."""
    from repowise.core.analysis.health.grading import (
        BAND_LABEL,
        BAND_ORDER,
        BAND_TERMINAL_COLOR,
    )

    bands = dist.get("bands") or {}
    if not dist.get("total_files"):
        return
    parts = []
    for band in BAND_ORDER:
        color = BAND_TERMINAL_COLOR[band]
        share = bands.get(band) or {}
        parts.append(
            f"[{color}]{share.get('pct', 0)}%[/{color}] {BAND_LABEL[band].lower()} "
            f"([dim]{share.get('files', 0)} files[/dim])"
        )
    console.print("[dim]Distribution (by code volume):[/dim] " + " · ".join(parts) + "\n")


def _render_badge(average_health: object, share: float | None = None) -> None:
    """Print ready-to-paste health-badge Markdown for a README.

    Emits a static shields badge for the current score (immediately usable) and
    documents the live endpoint form for a running Repowise server / hosted repo.
    """
    from repowise.core.analysis.health.grading import (
        BADGE_UNKNOWN_COLOR,
        BAND_BADGE_COLOR,
        format_score,
        headline_band,
    )

    if not isinstance(average_health, (int, float)):
        console.print("[yellow]No health score yet. Run `repowise init` first.[/yellow]")
        return
    band = headline_band(float(average_health), share)
    # Neutral, not green, when too little of the repo was analysed for a band.
    color = BAND_BADGE_COLOR[band] if band is not None else BADGE_UNKNOWN_COLOR
    msg = f"{format_score(float(average_health))}/10"
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
        from repowise.core.analysis.health.ranking import deduction_by_path

        stat = compute_defect_accuracy(
            report.metrics,
            report.findings,
            deductions=deduction_by_path(report.findings),
        )
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

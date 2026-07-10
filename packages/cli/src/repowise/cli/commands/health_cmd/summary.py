"""Console renderers for the top-of-report summary lines.

Performance-risk headline, band distribution, README badge, and the
"does the score find the bugs?" defect-accuracy line.
"""

from __future__ import annotations

from typing import Any

from repowise.cli.helpers import console


def _render_performance_section(report: Any, lang_by_path: dict[str, str]) -> None:
    """Honest performance headline: finding count + density + coverage + scope.

    Leads with the open-finding count and how much of the analyzed code a perf
    detector actually ran on, so a mostly-unsupported-language repo reads a low
    coverage % rather than a meaningless bounded 10/10. Silent when no code file
    carries a supported language (nothing to say).
    """
    from repowise.core.analysis.health.perf.coverage import coverage_for_metrics

    coverage = coverage_for_metrics(report.metrics, lang_by_path)
    if not coverage.analyzed_files:
        return
    perf_findings = sum(
        1 for f in report.findings if getattr(f, "dimension", "defect") == "performance"
    )
    perf_avg = report.kpis.get("performance_average")

    parts = [f"[bold]{perf_findings}[/bold] finding{'s' if perf_findings != 1 else ''}"]
    if coverage.covered_nloc > 0:
        density = round(10000.0 * perf_findings / coverage.covered_nloc, 2)
        parts.append(f"{density}/10K covered LOC")
    if isinstance(perf_avg, (int, float)):
        parts.append(f"avg {perf_avg:.1f}/10")
    console.print(
        "\n[bold]Performance risk[/bold] "
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
        "[dim]Scope: I/O-in-loop / N+1, resource/regex/defer-in-loop, blocking-in-async. "
        "Not covered: algorithmic blowups, GC pressure, ORM lazy-load N+1.[/dim]"
    )


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

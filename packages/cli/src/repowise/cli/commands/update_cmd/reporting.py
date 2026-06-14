"""Console rendering for ``repowise update``.

Pure presentation: takes finished generation data and prints the incremental
update report. No persistence or generation work happens here. The richer
panel/summary helpers added in the UX pass land here next to this report.
"""

from __future__ import annotations

from typing import Any

from repowise.cli.helpers import console


def _render_update_report(
    generated_pages: list,
    affected: Any,
    new_decision_markers: list,
    elapsed: float,
) -> None:
    """Render the incremental-update generation report (with a plain fallback)."""
    try:
        from repowise.core.generation.report import GenerationReport, render_report

        report = GenerationReport.from_pages(
            generated_pages,
            stale_count=len(affected.decay_only),
            decisions_count=len(new_decision_markers),
            elapsed=elapsed,
        )
        render_report(report, console)
    except Exception:
        # Fallback to simple message if report fails
        console.print(
            f"[bold green]Updated {len(generated_pages)} pages in {elapsed:.1f}s[/bold green]"
        )

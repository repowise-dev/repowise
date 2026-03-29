"""Generation report — structured summary of a generation run.

Provides token accounting, page breakdown by type, and cost estimation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import GeneratedPage


@dataclass
class GenerationReport:
    """Summary produced after ``generate_all`` completes."""

    pages_by_type: dict[str, int] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    stale_page_count: int = 0
    dead_code_findings_count: int = 0
    decisions_extracted: int = 0
    elapsed_seconds: float = 0.0
    hallucination_warning_count: int = 0

    @classmethod
    def from_pages(
        cls,
        pages: list[GeneratedPage],
        *,
        stale_count: int = 0,
        dead_code_count: int = 0,
        decisions_count: int = 0,
        elapsed: float = 0.0,
    ) -> GenerationReport:
        by_type = dict(Counter(p.page_type for p in pages))
        hal_count = sum(1 for p in pages if p.metadata.get("hallucination_warnings"))
        return cls(
            pages_by_type=by_type,
            total_input_tokens=sum(p.input_tokens for p in pages),
            total_output_tokens=sum(p.output_tokens for p in pages),
            total_cached_tokens=sum(p.cached_tokens for p in pages),
            stale_page_count=stale_count,
            dead_code_findings_count=dead_code_count,
            decisions_extracted=decisions_count,
            elapsed_seconds=elapsed,
            hallucination_warning_count=hal_count,
        )

    @property
    def total_pages(self) -> int:
        return sum(self.pages_by_type.values())

    def estimated_cost_usd(
        self,
        input_rate: float = 3.0,
        output_rate: float = 15.0,
    ) -> float:
        """Estimated USD cost.  Rates are per 1M tokens (Sonnet 4 defaults)."""
        return (
            self.total_input_tokens * input_rate + self.total_output_tokens * output_rate
        ) / 1_000_000


def render_report(report: GenerationReport, console: object) -> None:
    """Print a rich table summarising the generation run."""
    from rich.table import Table  # deferred so core has no hard rich dep

    table = Table(title="Generation Report", show_lines=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    for ptype, count in sorted(report.pages_by_type.items()):
        table.add_row(f"  {ptype}", str(count))
    table.add_row("[bold]Total pages[/bold]", f"[bold]{report.total_pages}[/bold]")
    table.add_row("Input tokens", f"{report.total_input_tokens:,}")
    table.add_row("Output tokens", f"{report.total_output_tokens:,}")
    if report.total_cached_tokens:
        table.add_row("Cached tokens", f"{report.total_cached_tokens:,}")
    table.add_row("Est. cost", f"${report.estimated_cost_usd():.4f}")
    table.add_row("Elapsed", f"{report.elapsed_seconds:.1f}s")
    if report.stale_page_count:
        table.add_row("Stale pages", f"[yellow]{report.stale_page_count}[/yellow]")
    if report.dead_code_findings_count:
        table.add_row("Dead code findings", str(report.dead_code_findings_count))
    if report.decisions_extracted:
        table.add_row("Decisions extracted", str(report.decisions_extracted))
    if report.hallucination_warning_count:
        table.add_row(
            "Hallucination warnings",
            f"[yellow]{report.hallucination_warning_count}[/yellow]",
        )

    console.print(table)  # type: ignore[union-attr]

"""Generation report — structured summary of a generation run.

Provides token accounting, page breakdown by type, and cost estimation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import GeneratedPage
from .page_overlap import OverlapReport, measure_orientation_overlap
from .page_tree import LayerGroupingReport, measure_layer_grouping
from .prose import prose_word_count

# How much prose the repository overview may ask a reader to get through.
# The page is the first thing anyone reads and it had grown past nine hundred
# words while saying what four hundred and fifty say.  The prompt asks for the
# same number; the check below reports whether the run honoured it.  Warn-only
# on purpose — a long overview is worth seeing, never worth failing a run over.
ORIENTATION_PROSE_WORD_BUDGET = 450


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
    self_repaired_page_count: int = 0
    # Vocabulary overlap across the orientation pages this run produced.
    # Warn-only: a flagged pair is reported, never fatal.  Read
    # ``comparable`` before reading ``flagged`` — an empty ``flagged`` on a
    # run that compared nothing is not a clean result.
    orientation_overlap: OverlapReport = field(default_factory=OverlapReport)
    # How far layer provenance reached across this run's pages.  Layers have no
    # pages of their own, so the docs tree groups by this metadata and nothing
    # raises when it is missing — the wiki just comes out flat.  Read
    # ``measured`` before reading the counts.
    layer_grouping: LayerGroupingReport = field(default_factory=LayerGroupingReport)
    # Tally of the generation-artifact checks run over provider responses this
    # process.  Carries its own denominator so "checked nothing" and "found
    # nothing" stay separate facts.
    artifact_checks: dict[str, int] = field(default_factory=dict)
    # Prose length of the repository overview this run produced, or ``None``
    # when the run produced no overview.  ``None`` and ``0`` are different
    # facts: nothing was measured, versus an overview with nothing in it.
    overview_prose_words: int | None = None

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
        # Deferred: the page generator pulls in the provider stack, and the
        # report is importable on its own (the CLI renders it lazily).
        from .page_generator.validation import artifact_check_counts

        by_type = dict(Counter(p.page_type for p in pages))
        hal_count = sum(1 for p in pages if p.metadata.get("hallucination_warnings"))
        repair_count = sum(1 for p in pages if p.metadata.get("self_repair"))
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
            self_repaired_page_count=repair_count,
            orientation_overlap=measure_orientation_overlap(pages),
            layer_grouping=measure_layer_grouping(pages),
            artifact_checks=artifact_check_counts(),
            overview_prose_words=next(
                (
                    prose_word_count(p.content or "")
                    for p in pages
                    if p.page_type == "repo_overview"
                ),
                None,
            ),
        )

    @property
    def total_pages(self) -> int:
        return sum(self.pages_by_type.values())

    @property
    def overview_over_budget(self) -> bool:
        """Whether the overview asks for more prose than the budget allows."""
        if self.overview_prose_words is None:
            return False
        return self.overview_prose_words > ORIENTATION_PROSE_WORD_BUDGET

    def overview_length_summary(self) -> str:
        """One line naming the overview's length against its budget."""
        if self.overview_prose_words is None:
            return "no overview in this run"
        line = f"{self.overview_prose_words} / {ORIENTATION_PROSE_WORD_BUDGET} words"
        return f"{line} — over budget" if self.overview_over_budget else line

    def artifact_check_summary(self) -> str:
        """One line saying whether the artifact checks ran, and what they found."""
        checked = self.artifact_checks.get("responses_checked", 0)
        if not checked:
            return "not run (0 responses checked)"
        return f"{checked} checked, {self.artifact_checks.get('rejected', 0)} rejected"

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
    if report.self_repaired_page_count:
        table.add_row("Self-repaired pages", str(report.self_repaired_page_count))

    # Always shown, including when nothing was comparable.  Hiding the row on
    # a zero would make "the check did not run" look like "the check passed".
    overlap = report.orientation_overlap
    overlap_text = overlap.summary_line()
    if overlap.flagged:
        overlap_text = f"[yellow]{overlap_text}[/yellow]"
    table.add_row("Orientation overlap", overlap_text)

    # Always shown, for the same reason as the row above: a hidden zero would
    # make "nothing was groupable" read as "everything grouped".
    grouping = report.layer_grouping
    grouping_text = grouping.summary_line()
    if grouping.ungrouped:
        grouping_text = f"[yellow]{grouping_text}[/yellow]"
    table.add_row("Layer grouping", grouping_text)

    # Same reasoning as the overlap row: shown even at zero, because a check
    # that never ran must not look like a check that passed.
    artifact_text = report.artifact_check_summary()
    if report.artifact_checks.get("rejected"):
        artifact_text = f"[yellow]{artifact_text}[/yellow]"
    table.add_row("Artifact checks", artifact_text)

    # Always shown for the same reason as the two rows above: a budget nobody
    # sees the reading of is a budget nobody keeps.
    length_text = report.overview_length_summary()
    if report.overview_over_budget:
        length_text = f"[yellow]{length_text}[/yellow]"
    table.add_row("Overview length", length_text)

    console.print(table)  # type: ignore[union-attr]

    for pair in overlap.flagged:
        console.print(f"  [yellow]overlap[/yellow] {pair.describe()}")  # type: ignore[union-attr]

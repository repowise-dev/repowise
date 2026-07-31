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

# The heading the deterministic templates put their question-shaped text under.
# Counted rather than asserted: the block is conditional by design, so a page
# without one is legitimate and only the ratio is meaningful.
QUESTIONS_HEADING = "## Questions this page answers"

# Page types rendered from a template with no model path, and therefore the
# ones that carry a deterministic questions block.
_QUESTION_PAGE_TYPES = ("file_page", "symbol_spotlight")


def _count_question_blocks(pages: list[GeneratedPage]) -> dict[str, int]:
    """How many template pages carry question-shaped text, out of how many.

    Both numbers, because the ratio is the only reading that means anything.
    A zero numerator on a zero denominator is a run that wrote no such pages;
    a zero numerator on a large denominator is the block having silently
    stopped rendering, which nothing else in the run would report.
    """
    eligible = [p for p in pages if p.page_type in _QUESTION_PAGE_TYPES]
    return {
        "eligible_pages": len(eligible),
        "with_questions": sum(1 for p in eligible if QUESTIONS_HEADING in (p.content or "")),
    }


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
    # Pages this run wrote but did not embed, because they say too little to be
    # worth one of the fixed number of rows retrieval fetches.  The page is
    # kept and still resolves as a link target; only its vector is withheld.
    # Zero whenever the information floor is off, which is the default.
    pages_denied_a_vector: int = 0
    # How far question-shaped text reached across this run's template-rendered
    # pages.  Carries its own denominator, so "this run wrote no such pages"
    # and "the block stopped rendering" stay separate facts.  Without the
    # denominator a silent template regression is indistinguishable from a run
    # that generated no file pages, and neither one raises.
    question_blocks: dict[str, int] = field(default_factory=dict)

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
        from ..persistence.information_floor import pages_denied_a_vector
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
            pages_denied_a_vector=pages_denied_a_vector(),
            question_blocks=_count_question_blocks(pages),
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

    @property
    def questions_missing(self) -> bool:
        """Whether a template page this run wrote came out with no questions.

        Warn-only, and expected to be non-zero: a file with no symbols and no
        resolved edges has nothing structural to ask about, so it legitimately
        renders none. What the flag is for is the other case — the number
        falling to zero, or near it, across a run that wrote thousands of
        pages, which is what a broken template looks like from outside.
        """
        eligible = self.question_blocks.get("eligible_pages", 0)
        return bool(eligible) and self.question_blocks.get("with_questions", 0) < eligible

    def overview_length_summary(self) -> str:
        """One line naming the overview's length against its budget."""
        if self.overview_prose_words is None:
            return "no overview in this run"
        line = f"{self.overview_prose_words} / {ORIENTATION_PROSE_WORD_BUDGET} words"
        return f"{line} — over budget" if self.overview_over_budget else line

    def question_block_summary(self) -> str:
        """One line saying how far question-shaped text reached this run."""
        eligible = self.question_blocks.get("eligible_pages", 0)
        if not eligible:
            return "not measured (0 template pages)"
        return f"{self.question_blocks.get('with_questions', 0)} of {eligible} pages"

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

    console.print(table)  # type: ignore[union-attr]
    render_generation_checks(report, console)


def render_generation_checks(report: GenerationReport, console: object) -> None:
    """Print the run's quality checks, each of them every time.

    Split out of the statistics table so a caller can show the checks without
    the cost and token detail. Both callers share this one definition: a check
    that renders in only one of two places is a check that goes silent the
    moment a run takes the other path.

    Every row prints even at zero. A hidden zero would make "the check did not
    run" indistinguishable from "the check passed", which is the failure the
    checks exist to prevent.
    """
    from rich.table import Table  # deferred so core has no hard rich dep

    table = Table(title="Generation Checks", show_lines=False)
    table.add_column("Check", style="cyan")
    table.add_column("Result", justify="right")

    overlap = report.orientation_overlap
    overlap_text = overlap.summary_line()
    if overlap.flagged:
        overlap_text = f"[yellow]{overlap_text}[/yellow]"
    table.add_row("Orientation overlap", overlap_text)

    grouping = report.layer_grouping
    grouping_text = grouping.summary_line()
    if grouping.ungrouped:
        grouping_text = f"[yellow]{grouping_text}[/yellow]"
    table.add_row("Layer grouping", grouping_text)

    artifact_text = report.artifact_check_summary()
    if report.artifact_checks.get("rejected"):
        artifact_text = f"[yellow]{artifact_text}[/yellow]"
    table.add_row("Artifact checks", artifact_text)

    length_text = report.overview_length_summary()
    if report.overview_over_budget:
        length_text = f"[yellow]{length_text}[/yellow]"
    table.add_row("Overview length", length_text)

    questions_text = report.question_block_summary()
    if report.questions_missing:
        questions_text = f"[yellow]{questions_text}[/yellow]"
    table.add_row("Question-shaped text", questions_text)

    console.print(table)  # type: ignore[union-attr]

    for pair in overlap.flagged:
        console.print(f"  [yellow]overlap[/yellow] {pair.describe()}")  # type: ignore[union-attr]

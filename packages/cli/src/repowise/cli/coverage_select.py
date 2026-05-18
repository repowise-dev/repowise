"""Interactive coverage-level chooser for ``repowise init``.

After ingestion completes but before the LLM run, the user picks how
thoroughly to document the repo. Counts are computed from the actual
ingestion result — not hard-coded heuristics — so the displayed
"pages at this level" always matches what generation will emit.
"""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from repowise.cli.cost_estimator import CoverageOption
from repowise.cli.ui import BRAND, BRAND_STYLE


# Columns shown in the coverage table. ``onboarding`` is constant
# across percentages (curated slots) but we include it so the user
# sees the full picture.
_DISPLAY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("file_page", "File"),
    ("symbol_spotlight", "Sym"),
    ("module_page", "Mod"),
    ("api_contract", "API"),
    ("infra_page", "Infra"),
    ("scc_page", "SCC"),
    ("onboarding", "Onb"),
)


def _format_pct(option: CoverageOption) -> str:
    label = f"{int(option.pct * 100)}%"
    if option.is_recommended:
        label += " (rec)"
    return label


def _format_cost(option: CoverageOption) -> str:
    est = option.estimate
    if est.cost_range is None:
        return f"${est.estimated_cost_usd:.2f}"
    return f"${est.cost_range.low:.2f}-${est.cost_range.high:.2f}"


def render_coverage_table(options: list[CoverageOption]) -> Table:
    """Build the Rich table shown to the user."""
    table = Table(
        title="[bold]Documentation coverage[/bold]",
        title_style="",
        border_style=BRAND,
        padding=(0, 1),
    )
    table.add_column("#", style=BRAND_STYLE, width=4)
    table.add_column("Coverage", style="bold")
    table.add_column("Pages", justify="right")
    for _, header in _DISPLAY_COLUMNS:
        table.add_column(header, justify="right")
    table.add_column("Est. cost", justify="right")

    for idx, opt in enumerate(options, start=1):
        row = [
            f"[{idx}]",
            _format_pct(opt),
            str(opt.estimate.total_pages),
        ]
        for page_type, _ in _DISPLAY_COLUMNS:
            row.append(str(opt.page_count_for(page_type)))
        row.append(_format_cost(opt))
        table.add_row(*row)

    return table


def interactive_coverage_select(
    console: Console,
    options: list[CoverageOption],
) -> CoverageOption:
    """Render the coverage table and prompt the user for a choice.

    Returns the selected :class:`CoverageOption`. The default is the
    one whose ``is_recommended`` flag is set, falling back to the
    middle of the list when none is flagged.
    """
    if not options:
        msg = "No coverage options provided"
        raise ValueError(msg)

    console.print(render_coverage_table(options))

    # Pick the default = recommended option's index, else the median.
    default_idx = next(
        (i for i, o in enumerate(options, start=1) if o.is_recommended),
        len(options) // 2 + 1,
    )

    choice = Prompt.ask(
        "  Select coverage",
        choices=[str(i) for i in range(1, len(options) + 1)],
        default=str(default_idx),
        console=console,
    )
    return options[int(choice) - 1]

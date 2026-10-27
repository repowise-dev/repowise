"""The run reports how often its module pages opened the same way.

A module page's opening sentence is its summary: ``_extract_summary`` stores it,
listings show it, and it is what stands as the module's description wherever the
wiki is summarised for a reader. When the model settles into one sentence frame,
the result is the same sentence presented as the description of a dozen
different subsystems — and nothing raises, because each page is individually
well-formed and every other check passes.

One run produced this, unflagged: "the presentation layer is the" opened ten of
eighty-nine module pages, "the transport adapter layer serves" another three,
and nineteen pages in total shared their first five words with another page.

The prompt now bans the frames, and this counter is what says whether the ban
held. Warn-only and reported with its denominator, because a run with one
module page cannot repeat itself and its zero means nothing.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

from repowise.core.generation.context.contexts import ModulePageContext
from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.report import (
    GenerationReport,
    measure_opening_frames,
)


def _page(page_id: str, content: str, page_type: str = "module_page") -> GeneratedPage:
    return GeneratedPage(
        page_id=page_id,
        page_type=page_type,
        title=page_id,
        content=content,
        source_hash="",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=3,
        target_path="",
        created_at="2026-08-01T00:00:00Z",
        updated_at="2026-08-01T00:00:00Z",
    )


_FRAMED = [
    "# Ingestion\n\nThe presentation layer is the surface every request crosses.\n",
    "# Resolution\n\nThe presentation layer is the boundary that resolves references.\n",
    "# Storage\n\nThe presentation layer is the place rows are written.\n",
]

_VARIED = [
    "# Ingestion\n\nParsing turns a checkout into symbols the graph can hold.\n",
    "# Resolution\n\nCross-file references become edges here, or they become nothing.\n",
    "# Storage\n\nRows land in SQLite and vectors in LanceDB, on two schedules.\n",
]


def test_a_set_of_same_opening_pages_is_flagged():
    """The failure the check exists for: one sentence, three subsystems."""
    report = measure_opening_frames([_page(f"m{i}", c) for i, c in enumerate(_FRAMED)])

    assert report.eligible_pages == 3
    assert report.repeated_pages == 3
    assert report.top_frame == "the presentation layer is the"
    assert report.top_frame_count == 3
    assert report.over_budget is True


def test_a_varied_set_passes():
    report = measure_opening_frames([_page(f"m{i}", c) for i, c in enumerate(_VARIED)])

    assert report.repeated_pages == 0
    assert report.repeat_ratio == 0.0
    assert report.top_frame == ""
    assert report.over_budget is False


def test_only_module_pages_are_compared():
    """File pages and spotlights are template-rendered and open identically on
    purpose; counting them would swamp the number this check is about."""
    pages = [
        _page("f1", "# A\n\nThis page documents `a.py`.\n", page_type="file_page"),
        _page("f2", "# B\n\nThis page documents `b.py`.\n", page_type="file_page"),
        *[_page(f"m{i}", c) for i, c in enumerate(_VARIED)],
    ]

    report = measure_opening_frames(pages)

    assert report.eligible_pages == 3
    assert report.repeated_pages == 0


def test_a_single_module_page_reports_not_measured():
    """A page cannot repeat itself, so its zero is no result rather than a
    clean one — and the two read identically without ``measured``."""
    report = measure_opening_frames([_page("m0", _FRAMED[0])])

    assert report.measured is False
    assert report.over_budget is False
    assert "not measured" in report.summary_line()


def test_headings_and_tables_are_not_the_opening():
    """The opening is the first prose. A page whose first lines are a heading
    and a table must be compared on the sentence, not on the table row."""
    content = (
        "# Ingestion\n\n"
        "| Concept | Symbol | File |\n| --- | --- | --- |\n| A | `A` | `a.py` |\n\n"
        "Parsing turns a checkout into symbols the graph can hold.\n"
    )

    report = measure_opening_frames([_page("m0", content), _page("m1", content)])

    assert report.top_frame == "parsing turns a checkout into"


def test_the_report_carries_the_measurement_and_the_check_prints():
    from io import StringIO

    from rich.console import Console

    from repowise.core.generation.report import render_generation_checks

    report = GenerationReport.from_pages([_page(f"m{i}", c) for i, c in enumerate(_FRAMED)])
    assert report.opening_frames.repeated_pages == 3

    buffer = StringIO()
    render_generation_checks(report, Console(file=buffer, width=120))
    output = buffer.getvalue()

    assert "Module page openings" in output
    # Every check prints at zero too: a hidden row makes "did not run" and
    # "passed" the same thing on screen.
    empty = StringIO()
    render_generation_checks(GenerationReport.from_pages([]), Console(file=empty, width=120))
    assert "Module page openings" in empty.getvalue()


@pytest.fixture
def module_prompt() -> str:
    templates = (
        Path(__file__).parents[3]
        / "packages"
        / "core"
        / "src"
        / "repowise"
        / "core"
        / "generation"
        / "templates"
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    ctx = ModulePageContext(
        title="Resolution Layer",
        language="python",
        total_symbols=4,
        public_symbols=3,
        entry_points=[],
        dependencies=[],
        dependents=[],
        pagerank_mean=0.001,
        files=["core/resolvers/context.py"],
        scope="Covers reference resolution; leaves parsing to its sibling.",
    )
    return env.get_template("module_page.j2").render(ctx=ctx, module_git_summary=None)


def test_the_prompt_bans_the_frames_the_corpus_settled_into(module_prompt):
    """Asserted on the rendered prompt, which is this template's output."""
    assert "serves as the" in module_prompt
    assert "entry stage" in module_prompt
    assert "it consumes" in module_prompt
    assert "same first five words" in module_prompt


def test_the_prompt_no_longer_points_at_an_opener_it_never_describes(module_prompt):
    """The instruction used to read "the role-leading opener described" — and
    nothing in the prompt described one. An opener the model has to invent is
    how a run ends up with eighty-nine pages sharing three sentences."""
    assert "role-leading opener described" not in module_prompt

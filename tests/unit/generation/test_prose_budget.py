"""The repository overview's prose budget, and the report row that checks it.

The overview is the page every reader meets first, and it had grown to roughly
a thousand words of prose saying what four hundred said.  The budget is asked
for in the prompt; this is the check that says whether the run honoured it.
"""

from __future__ import annotations

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.prose import prose_word_count
from repowise.core.generation.report import (
    ORIENTATION_PROSE_WORD_BUDGET,
    GenerationReport,
)

BUDGET = ORIENTATION_PROSE_WORD_BUDGET


def _page(content: str, page_type: str = "repo_overview") -> GeneratedPage:
    return GeneratedPage(
        page_id=f"{page_type}:.",
        page_type=page_type,
        title="Repository Overview",
        content=content,
        source_hash="",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=6,
        target_path="",
        created_at="2026-07-29T00:00:00Z",
        updated_at="2026-07-29T00:00:00Z",
    )


def _overview_of(word_count: int) -> GeneratedPage:
    body = " ".join(f"word{index}" for index in range(word_count - 1))
    return _page(f"# Overview\n\n{body}\n")


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_prose_word_count_ignores_code_blocks_and_tables() -> None:
    content = """# Overview

The loader reads three files.

```python
these words are quoted source and are not prose the reader wades through
```

| Field | Type |
| --- | --- |
| path | string |
"""
    # "Overview" plus "The loader reads three files."
    assert prose_word_count(content) == 6


def test_prose_word_count_ignores_bare_punctuation() -> None:
    assert prose_word_count("- one — two\n\n* three\n") == 3


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


def test_overview_at_the_cap_is_within_budget() -> None:
    report = GenerationReport.from_pages([_overview_of(BUDGET)])

    assert report.overview_prose_words == BUDGET
    assert not report.overview_over_budget
    assert report.overview_length_summary() == f"{BUDGET} / {BUDGET} words"


def test_overview_just_under_the_cap_is_within_budget() -> None:
    report = GenerationReport.from_pages([_overview_of(BUDGET - 1)])

    assert report.overview_prose_words == BUDGET - 1
    assert not report.overview_over_budget


def test_overview_just_over_the_cap_is_flagged() -> None:
    report = GenerationReport.from_pages([_overview_of(BUDGET + 1)])

    assert report.overview_prose_words == BUDGET + 1
    assert report.overview_over_budget
    assert "over budget" in report.overview_length_summary()


def test_a_run_without_an_overview_says_so_rather_than_reading_as_a_pass() -> None:
    report = GenerationReport.from_pages([_page("# Layer\n\nwords.", page_type="layer_page")])

    assert report.overview_prose_words is None
    assert not report.overview_over_budget
    assert report.overview_length_summary() == "no overview in this run"


# ---------------------------------------------------------------------------
# Prompt and check must agree
# ---------------------------------------------------------------------------


def test_the_prompt_states_the_same_cap_the_report_checks() -> None:
    """A budget the prompt never mentions would only ever be reported, never met."""
    from pathlib import Path

    template = (
        Path(__file__).parents[3]
        / "packages/core/src/repowise/core/generation/templates/repo_overview.j2"
    )
    assert str(ORIENTATION_PROSE_WORD_BUDGET) in template.read_text()

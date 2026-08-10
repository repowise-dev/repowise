"""The run reports how far question-shaped text reached.

The block is rendered by a template, so nothing raises when it stops being
rendered — a regression would look like an ordinary successful run producing
slightly shorter pages, and the only symptom would be retrieval quietly
getting worse on the next index. This counter is what makes that visible.

It carries its own denominator because the numerator alone cannot be read: a
zero is either "this run wrote no template pages" or "the block is broken",
and those need different responses.
"""

from __future__ import annotations

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.report import QUESTIONS_HEADING, GenerationReport

_WITH = f"# A file\n\n## Overview\n\nText.\n\n{QUESTIONS_HEADING}\n\n- What imports `a.py`?\n"
_WITHOUT = "# A file\n\n## Overview\n\nText.\n"


def _page(page_id: str, page_type: str, content: str) -> GeneratedPage:
    return GeneratedPage(
        page_id=page_id,
        page_type=page_type,
        title=page_id,
        content=content,
        source_hash="",
        model_name="template",
        provider_name="template",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=2,
        target_path="",
        created_at="2026-07-31T00:00:00Z",
        updated_at="2026-07-31T00:00:00Z",
    )


def test_the_report_counts_pages_carrying_questions():
    report = GenerationReport.from_pages(
        [
            _page("file_page:a.py", "file_page", _WITH),
            _page("file_page:b.py", "file_page", _WITHOUT),
            _page("symbol_spotlight:a.py::F", "symbol_spotlight", _WITH),
        ]
    )

    assert report.question_blocks == {"eligible_pages": 3, "with_questions": 2}
    assert report.question_block_summary() == "2 of 3 pages"


def test_a_run_with_no_template_pages_says_so_rather_than_reporting_zero():
    """ "Nothing was measured" and "nothing was found" are different facts.

    Without the denominator, a run that generated only model pages would look
    identical to one where every template lost its questions block.
    """
    report = GenerationReport.from_pages([_page("repo_overview:.", "repo_overview", _WITHOUT)])

    assert report.question_blocks == {"eligible_pages": 0, "with_questions": 0}
    assert report.question_block_summary() == "not measured (0 template pages)"
    assert report.questions_missing is False


def test_the_check_flags_a_run_where_the_block_stopped_rendering():
    """The failure this exists to catch: eligible pages, no questions on them."""
    report = GenerationReport.from_pages(
        [
            _page("file_page:a.py", "file_page", _WITHOUT),
            _page("file_page:b.py", "file_page", _WITHOUT),
        ]
    )

    assert report.question_blocks == {"eligible_pages": 2, "with_questions": 0}
    assert report.questions_missing is True


def test_a_full_run_is_not_flagged():
    report = GenerationReport.from_pages([_page("file_page:a.py", "file_page", _WITH)])

    assert report.questions_missing is False


def test_the_check_prints_even_when_it_measured_nothing():
    """Every check renders every time.

    A hidden zero makes "the check did not run" indistinguishable from "the
    check passed", which is the whole reason the checks table prints rows at
    zero rather than skipping them.
    """
    from io import StringIO

    from rich.console import Console

    from repowise.core.generation.report import render_generation_checks

    buffer = StringIO()
    render_generation_checks(GenerationReport.from_pages([]), Console(file=buffer, width=100))

    assert "Question-shaped text" in buffer.getvalue()

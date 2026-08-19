"""The run reports how far the module concept index reached.

The table is appended in code, after the provider call, which is a quieter
place to lose something than a template. An early return on an error path, or a
refactor that hands back the page before the append, leaves every module page
pure prose again — and every template test in the suite still passes, because
none of the templates changed. This counter is the only thing that would say so.

Its own denominator, for the same reason the questions counter carries one: a
bare zero is either "this run wrote no module pages" or "the append stopped",
and those need different responses.
"""

from __future__ import annotations

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.report import CONCEPT_INDEX_HEADING, GenerationReport

_WITH = (
    "# Resolution Layer\n\nProse.\n\n"
    f"{CONCEPT_INDEX_HEADING}\n\n"
    "| Concept | Symbol | File |\n| --- | --- | --- |\n"
    "| Resolver context | `ResolverContext` | `a.py` |\n"
)
_WITHOUT = "# Resolution Layer\n\nProse.\n"


def _page(page_id: str, page_type: str, content: str) -> GeneratedPage:
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


def test_the_report_counts_module_pages_carrying_their_identifiers():
    report = GenerationReport.from_pages(
        [
            _page("module_page:a", "module_page", _WITH),
            _page("module_page:b", "module_page", _WITHOUT),
            _page("file_page:c.py", "file_page", _WITHOUT),
        ]
    )

    assert report.concept_indexes == {"eligible_pages": 2, "with_index": 1}
    assert report.concept_index_summary() == "1 of 2 pages"


def test_a_run_with_no_module_pages_says_so_rather_than_reporting_zero():
    report = GenerationReport.from_pages([_page("file_page:a.py", "file_page", _WITHOUT)])

    assert report.concept_indexes == {"eligible_pages": 0, "with_index": 0}
    assert report.concept_index_summary() == "not measured (0 module pages)"
    assert report.concept_indexes_missing is False


def test_the_check_flags_a_run_where_the_append_stopped():
    """The failure this exists to catch: module pages written, none of them
    carrying the identifiers they were supposed to gain."""
    report = GenerationReport.from_pages(
        [
            _page("module_page:a", "module_page", _WITHOUT),
            _page("module_page:b", "module_page", _WITHOUT),
        ]
    )

    assert report.concept_indexes == {"eligible_pages": 2, "with_index": 0}
    assert report.concept_indexes_missing is True


def test_a_full_run_is_not_flagged():
    report = GenerationReport.from_pages([_page("module_page:a", "module_page", _WITH)])

    assert report.concept_indexes_missing is False


def test_the_check_prints_even_when_it_measured_nothing():
    from io import StringIO

    from rich.console import Console

    from repowise.core.generation.report import render_generation_checks

    buffer = StringIO()
    render_generation_checks(GenerationReport.from_pages([]), Console(file=buffer, width=100))

    assert "Module concept index" in buffer.getvalue()

"""Vocabulary overlap between orientation pages."""

from __future__ import annotations

import logging

import pytest

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_overlap import (
    ORIENTATION_PAGE_TYPES,
    OverlapReport,
    jaccard,
    measure_orientation_overlap,
)
from repowise.core.generation.report import GenerationReport

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# The overview and the architecture diagram are the known duplicate pair: both
# describe the same repository at the same altitude, in the same words.
_OVERVIEW = """
Repowise is a repository documentation engine. It ingests a codebase and its
git metadata, parses the source into structured entities, resolves the
cross-references and dependencies between them, and generates wiki pages and
graph data that an API serves to a web UI. The ingestion layer walks the tree,
the analysis layer scores health and risk, and the generation layer synthesises
the prose. Persistence keeps the parsed entities and the generated pages.
"""

_ARCHITECTURE_DIAGRAM = """
This diagram shows the repowise documentation engine. A codebase and its git
metadata are ingested, the source is parsed into structured entities, the
cross-references and dependencies between them are resolved, and wiki pages
and graph data are generated for an API that serves a web UI. Ingestion walks
the tree, analysis scores health and risk, generation synthesises the prose,
and persistence keeps the parsed entities and the generated pages.
"""

# Genuinely different: a first-run walkthrough shares almost no vocabulary with
# an architectural description.
_GETTING_STARTED = """
Install the command line tool with pip. Point it at a checkout and run the
index command; the first pass takes a few minutes on a large tree. When it
finishes, open the local server in a browser to read what it wrote. If you
would rather stay in your editor, install the extension and use the sidebar.
Set your API key in the environment first or the run will stop and tell you.
"""


def _page(
    page_id: str,
    page_type: str,
    title: str,
    content: str,
) -> GeneratedPage:
    return GeneratedPage(
        page_id=page_id,
        page_type=page_type,
        title=title,
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


@pytest.fixture
def overview() -> GeneratedPage:
    return _page("repo_overview:.", "repo_overview", "Repository Overview", _OVERVIEW)


@pytest.fixture
def architecture() -> GeneratedPage:
    return _page(
        "architecture_diagram:.",
        "architecture_diagram",
        "Architecture Diagram",
        _ARCHITECTURE_DIAGRAM,
    )


@pytest.fixture
def getting_started() -> GeneratedPage:
    return _page(
        "onboarding:getting-started",
        "onboarding",
        "Getting Started",
        _GETTING_STARTED,
    )


# ---------------------------------------------------------------------------
# The metric
# ---------------------------------------------------------------------------


def test_flags_the_overview_and_architecture_duplicate(overview, architecture):
    """Two pages describing the same thing in the same words are flagged."""
    report = measure_orientation_overlap([overview, architecture])

    assert report.comparable
    assert report.pairs_compared == 1
    assert report.flagged_count == 1

    pair = report.flagged[0]
    assert {pair.page_type_a, pair.page_type_b} == {
        "repo_overview",
        "architecture_diagram",
    }
    assert pair.similarity >= report.cross_type_threshold


def test_does_not_flag_genuinely_different_pages(overview, getting_started):
    """A walkthrough and an overview are compared, and pass."""
    report = measure_orientation_overlap([overview, getting_started])

    assert report.comparable
    assert report.pairs_compared == 1
    assert report.flagged_count == 0
    assert report.highest_similarity == 0.0


def test_pair_records_the_threshold_it_was_judged_against(overview, architecture):
    """A cross-type pair is judged by the cross-type threshold."""
    report = measure_orientation_overlap([overview, architecture])

    pair = report.flagged[0]
    assert pair.same_type is False
    assert pair.threshold == report.cross_type_threshold


def test_same_type_pages_are_held_to_their_own_threshold():
    """Pages of one type share a house style, so they get a higher bar.

    One body of text against another, identical in both runs — flagged when
    the two arrive as different page types, not flagged when they arrive as
    two pages of the same type, because the same-type bar sits higher.
    """
    thresholds = {"cross_type_threshold": 0.22, "same_type_threshold": 0.45}

    # Two texts overlapping by exactly 1/3 — above the cross-type bar, below
    # the same-type one, so the routing is what decides the outcome.
    left = " ".join(f"word{i}" for i in range(30))
    right = " ".join(f"word{i}" for i in range(15, 45))

    as_different_types = measure_orientation_overlap(
        [
            _page("repo_overview:.", "repo_overview", "Overview", left),
            _page("architecture_diagram:.", "architecture_diagram", "Diagram", right),
        ],
        **thresholds,
    )
    as_same_type = measure_orientation_overlap(
        [
            _page("layer_page:one", "layer_page", "Layer: One", left),
            _page("layer_page:two", "layer_page", "Layer: Two", right),
        ],
        **thresholds,
    )

    # Both runs compared the same text, so the score is identical; only the
    # bar it is measured against differs.
    assert as_same_type.pairs_compared == as_different_types.pairs_compared == 1
    assert as_different_types.flagged_count == 1
    assert as_different_types.flagged[0].threshold == 0.22
    assert as_same_type.flagged_count == 0


def test_non_orientation_pages_are_ignored(overview):
    """Reference pages are not part of the orientation set."""
    file_page = _page("file_page:a.py", "file_page", "a.py", _OVERVIEW)
    other_file = _page("file_page:b.py", "file_page", "b.py", _ARCHITECTURE_DIAGRAM)

    report = measure_orientation_overlap([overview, file_page, other_file])

    assert report.pages_compared == 1
    assert report.pairs_compared == 0
    assert not report.comparable


# ---------------------------------------------------------------------------
# The not-run case must not read as a pass
# ---------------------------------------------------------------------------


def test_empty_orientation_set_is_not_a_pass():
    """Zero pairs because there was nothing to compare is not zero overlap."""
    report = measure_orientation_overlap([])

    assert report.flagged_count == 0
    assert report.pairs_compared == 0
    assert report.comparable is False
    assert report.highest_similarity is None
    assert "not computed" in report.summary_line()


def test_no_overlap_is_distinguishable_from_nothing_compared(overview, getting_started):
    """The two zeroes report differently."""
    nothing_compared = measure_orientation_overlap([])
    nothing_overlapped = measure_orientation_overlap([overview, getting_started])

    assert nothing_compared.flagged_count == nothing_overlapped.flagged_count == 0
    assert nothing_compared.comparable is False
    assert nothing_overlapped.comparable is True
    assert nothing_compared.summary_line() != nothing_overlapped.summary_line()


def test_empty_orientation_set_warns(caplog):
    """A check that compared nothing says so out loud."""
    with caplog.at_level(logging.WARNING):
        measure_orientation_overlap([])

    assert any("compared no pairs" in r.message for r in caplog.records)


def test_flagged_pair_warns(caplog, overview, architecture):
    with caplog.at_level(logging.WARNING):
        measure_orientation_overlap([overview, architecture])

    assert any("overlap" in r.message.lower() for r in caplog.records)


def test_page_without_content_is_skipped_not_scored(overview):
    """An empty page shares nothing with everything; that is not a result."""
    blank = _page("onboarding:blank", "onboarding", "Blank", "")

    report = measure_orientation_overlap([overview, blank])

    assert report.pages_skipped_empty == 1
    assert report.pages_compared == 1
    assert report.pairs_compared == 0
    assert not report.comparable


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


def test_threshold_is_configurable(overview, architecture):
    strict = measure_orientation_overlap([overview, architecture], cross_type_threshold=0.01)
    lenient = measure_orientation_overlap([overview, architecture], cross_type_threshold=0.99)

    assert strict.flagged_count == 1
    assert lenient.flagged_count == 0
    assert lenient.comparable, "a lenient threshold still compares the pair"


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_nonsense_threshold_raises(bad, overview, architecture):
    with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
        measure_orientation_overlap([overview, architecture], cross_type_threshold=bad)

    with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
        measure_orientation_overlap([overview, architecture], same_type_threshold=bad)


def test_jaccard_of_two_empty_sets_is_zero():
    assert jaccard([], []) == 0.0


def test_orientation_types_are_the_pages_read_before_reference_content():
    assert set(ORIENTATION_PAGE_TYPES) == {
        "onboarding",
        "layer_page",
        "repo_overview",
        "architecture_diagram",
    }


# ---------------------------------------------------------------------------
# Surfaced on the generation report
# ---------------------------------------------------------------------------


def test_generation_report_carries_the_overlap_result(overview, architecture):
    report = GenerationReport.from_pages([overview, architecture])

    assert isinstance(report.orientation_overlap, OverlapReport)
    assert report.orientation_overlap.flagged_count == 1
    assert report.orientation_overlap.comparable


def test_generation_report_overlap_defaults_to_not_computed():
    """A report built from no pages must not claim a clean orientation set."""
    report = GenerationReport.from_pages([])

    assert report.orientation_overlap.comparable is False
    assert report.orientation_overlap.flagged_count == 0

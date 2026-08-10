"""One embed recipe, shared by everything that writes a page's vector.

Four commands write vectors for a wiki page — generation, ``reindex``,
``doctor --repair`` and the hosted indexer — and each used to build its own
text. One embedded the content alone, one prefixed the title, none carried
the path or the summary. A vector was therefore not comparable with another
vector: whether a page could be found by its own name depended on which
command last wrote it, and nothing reported the difference.

These pin the recipe itself and then pin each caller to it, because a shared
helper that one caller quietly stops using is the same bug again.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.vector_store import embed_item
from repowise.core.persistence.vector_store._base import STORED_SNIPPET_CHARS

PAGE = {
    "page_id": "file_page:packages/core/search.py",
    "title": "File: packages/core/search.py",
    "page_type": "file_page",
    "target_path": "packages/core/search.py",
    "summary": "Full-text search over the wiki index.",
    "content": "## Overview\n\nBuilds the query and ranks the rows.",
}


def test_the_recipe_is_title_then_path_then_summary_then_content():
    """Pinned byte for byte — the text is the whole product of this helper."""
    page_id, text, _meta = embed_item(
        PAGE["page_id"], **{k: v for k, v in PAGE.items() if k != "page_id"}
    )

    assert page_id == "file_page:packages/core/search.py"
    assert text == (
        "File: packages/core/search.py\n"
        "packages/core/search.py\n"
        "Full-text search over the wiki index.\n"
        "## Overview\n\nBuilds the query and ranks the rows."
    )


def test_the_path_reaches_the_vector():
    """The reason the path is in the text at all.

    A page about ``search.py`` has no idea it is about ``search.py`` unless
    its prose happens to say so, and prose rarely repeats its own filename.
    """
    _pid, text, _meta = embed_item(
        "file_page:a/b/widget.py",
        title="File: a/b/widget.py",
        page_type="file_page",
        target_path="a/b/widget.py",
        summary="",
        content="This module does a thing.",
    )

    assert "a/b/widget.py" in text
    assert "widget.py" not in "This module does a thing."


def test_an_absent_field_leaves_no_blank_line():
    """Empty fields drop out rather than padding the text with separators.

    Two pages with the same words must embed to the same string whether or
    not one of them happens to carry a summary.
    """
    _pid, text, _meta = embed_item(
        "repo_overview:repowise",
        title="Overview",
        page_type="repo_overview",
        target_path="",
        summary="",
        content="Body.",
    )

    assert text == "Overview\nBody."


def test_a_blank_title_raises():
    """A titleless row looks healthy and cannot be found by name.

    Accepting it writes a vector whose only defect shows up as an absence in
    someone's search results months later, which is why this is a raise and
    not a warning.
    """
    with pytest.raises(ValueError, match="no title"):
        embed_item(
            "file_page:a.py",
            title="   ",
            page_type="file_page",
            target_path="a.py",
            summary="",
            content="Body.",
        )


def test_the_metadata_carries_every_field_a_store_reads():
    _pid, _text, meta = embed_item(
        PAGE["page_id"], **{k: v for k, v in PAGE.items() if k != "page_id"}
    )

    assert meta == {
        "title": "File: packages/core/search.py",
        "page_type": "file_page",
        "target_path": "packages/core/search.py",
        "summary": "Full-text search over the wiki index.",
        "content": "## Overview\n\nBuilds the query and ranks the rows.",
    }


def test_the_stored_content_is_wide_enough_for_an_evidence_window():
    """The store cuts at ``STORED_SNIPPET_CHARS``; the recipe must reach it.

    A store that keeps 2,000 characters of a page it was only ever handed 600
    of keeps 600. The widening is only real if the recipe hands over that
    much, which is what this pins.
    """
    _pid, _text, meta = embed_item(
        "file_page:big.py",
        title="File: big.py",
        page_type="file_page",
        target_path="big.py",
        summary="",
        content="y" * (STORED_SNIPPET_CHARS + 500),
    )

    assert len(meta["content"]) == STORED_SNIPPET_CHARS


def test_generation_produces_exactly_the_shared_recipe():
    from repowise.core.generation.models import GeneratedPage
    from repowise.core.generation.page_generator.orchestrate import _embed_item

    page = GeneratedPage(
        page_id=PAGE["page_id"],
        page_type=PAGE["page_type"],
        title=PAGE["title"],
        content=PAGE["content"],
        source_hash="",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=1,
        target_path=PAGE["target_path"],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        summary=PAGE["summary"],
    )

    assert _embed_item(page) == embed_item(
        PAGE["page_id"], **{k: v for k, v in PAGE.items() if k != "page_id"}
    )


def test_generation_embeds_the_summary_the_index_was_given():
    """Both arms must describe a page the same way.

    Generation used to derive its own summary for the vector while the
    full-text index was given ``page.summary``. The two differ, so a page's
    summary depended on which arm you asked.
    """
    from repowise.core.generation.models import GeneratedPage
    from repowise.core.generation.page_generator.orchestrate import _embed_item

    page = GeneratedPage(
        page_id="file_page:a.py",
        page_type="file_page",
        title="File: a.py",
        content="## Overview\n\nSomething else entirely.",
        source_hash="",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=1,
        target_path="a.py",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        summary="The stored summary.",
    )

    _pid, text, meta = _embed_item(page)

    assert "The stored summary." in text
    assert meta["summary"] == "The stored summary."

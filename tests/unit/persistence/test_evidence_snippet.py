"""The evidence snippet shows the part of the page the query matched.

``search_codebase`` puts this string in front of the caller as its reason for
returning a hit, and the answer tool's coverage re-ranker reads it when
ordering candidates. Both were being handed the first 200 characters of the
page, which on a generated page is the same ``## Overview`` opener every time —
identical across thousands of hits, and never the passage that matched.

A window centred on the match says why the page is here. The opener remains
the fallback, because a hit with no matching term still has to show something.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.search import FullTextSearch, snippet_around

_OPENER = "## Overview\n\nThis module is part of the indexing pipeline. " + "Filler. " * 40
_MATCH = "The walker refuses to descend into a nested git checkout."
_TAIL = " Trailing prose. " * 40


@pytest.fixture
async def fts(async_engine):
    fs = FullTextSearch(async_engine)
    await fs.ensure_index()
    return fs


class TestSnippetAround:
    def test_a_matching_term_centres_the_window(self):
        content = _OPENER + _MATCH + _TAIL

        window = snippet_around(content, "nested git checkout")

        assert "nested git checkout" in window
        assert not window.startswith("## Overview")

    def test_no_match_falls_back_to_the_opener(self):
        """A hit with no matching term still has to show something."""
        content = _OPENER + _MATCH

        window = snippet_around(content, "xylophone tuning")

        assert window.startswith("## Overview")

    def test_an_empty_query_falls_back_to_the_opener(self):
        content = _OPENER + _MATCH
        assert snippet_around(content, "").startswith("## Overview")

    def test_the_window_is_capped(self):
        content = _OPENER + _MATCH + _TAIL
        assert len(snippet_around(content, "nested")) <= 200

    def test_a_short_page_is_returned_whole(self):
        assert snippet_around("Tiny page about walkers.", "walkers") == "Tiny page about walkers."

    def test_an_empty_page_returns_empty(self):
        assert snippet_around("", "walkers") == ""

    def test_a_match_near_the_start_does_not_produce_a_short_window(self):
        """Centring must not run off the front and truncate the window."""
        content = "The walker stops here. " + _TAIL

        window = snippet_around(content, "walker")

        assert "walker" in window
        assert len(window) > 150

    def test_a_match_near_the_end_does_not_produce_a_short_window(self):
        content = _TAIL + "The walker stops here."

        window = snippet_around(content, "walker")

        assert "walker" in window
        assert len(window) > 150

    def test_matching_is_case_insensitive(self):
        content = _OPENER + "The WALKER refuses to descend." + _TAIL
        assert "WALKER" in snippet_around(content, "walker")

    def test_a_stop_word_alone_does_not_steer_the_window(self):
        """ "the" appears in the opener; it must not count as a match.

        Otherwise every query containing a common word centres on the opener
        and the change buys nothing.
        """
        content = _OPENER + _MATCH + _TAIL
        assert snippet_around(content, "the").startswith("## Overview")


class TestFullTextSnippet:
    async def test_a_full_text_hit_shows_the_matching_region(self, fts):
        await fts.index(
            "p1",
            "File: walker.py",
            _OPENER + _MATCH + _TAIL,
            summary="Walks the tree.",
            target_path="src/walker.py",
        )

        results = await fts.search("nested git checkout")

        assert len(results) == 1
        assert "nested git checkout" in results[0].snippet

    async def test_a_hit_matched_only_by_its_path_shows_the_opener(self, fts):
        """The window comes from the content, so a path-only match has none."""
        await fts.index(
            "p1",
            "Walker",
            _OPENER,
            summary="Walks the tree.",
            target_path="src/telemetry/collector.py",
        )

        results = await fts.search("telemetry collector")

        assert len(results) == 1
        assert results[0].snippet.startswith("## Overview")

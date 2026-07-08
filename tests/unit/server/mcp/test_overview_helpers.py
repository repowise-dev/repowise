"""Unit tests for get_overview's pure rendering helpers."""

from __future__ import annotations

from repowise.server.mcp_server.tool_overview import (
    _compact_overview_content,
    _dedupe_tour_steps,
    _module_description,
    _truncate_at_word,
)


class TestTruncateAtWord:
    def test_short_unchanged(self):
        assert _truncate_at_word("short", 120) == "short"

    def test_cuts_at_word_boundary_with_ellipsis(self):
        text = (
            "Implements the server-side application logic for MCP tooling, "
            "including budget/risk/meta helpers and request/response handling"
        )
        out = _truncate_at_word(text, 120)
        assert out.endswith("…")
        # No mid-word fragment: every word emitted exists in the source.
        for word in out.rstrip("…").split():
            assert word in text


class TestModuleDescription:
    def test_strips_overview_boilerplate(self):
        content = "## Overview\n\nThe `core/distill` module is the distillation subsystem."
        out = _module_description(content)
        assert not out.startswith("##")
        assert out.startswith("The `core/distill` module")

    def test_truncates_long_prose_at_word(self):
        content = "## Overview\n\n" + ("word " * 100)
        out = _module_description(content, limit=50)
        assert len(out) <= 51
        assert out.endswith("…")

    def test_empty_content(self):
        assert _module_description("") == ""


class TestCompactOverviewContent:
    def test_keeps_only_first_section(self):
        content = (
            "## Project Summary\n\n"
            "Repowise ingests a repo and produces documentation.\n\n"
            "## Technology Stack\n\n"
            "- Python\n- TypeScript\n\n"
            "## Architecture\n\nA four-stage pipeline."
        )
        out = _compact_overview_content(content)
        assert out.startswith("## Project Summary")
        assert "Repowise ingests a repo" in out
        assert "Technology Stack" not in out
        assert "Architecture" not in out

    def test_single_section_unchanged(self):
        content = "## Project Summary\n\nJust the one section here."
        assert _compact_overview_content(content) == content

    def test_empty_content(self):
        assert _compact_overview_content("") == ""


class TestDedupeTourSteps:
    def test_collapses_consecutive_identical_kind_and_reason(self):
        tour = [
            {"title": "a.ts", "kind": "code", "reason": "A re-export hub."},
            {"title": "b.ts", "kind": "code", "reason": "A re-export hub."},
            {"title": "c.ts", "kind": "code", "reason": "A re-export hub."},
            {"title": "d.py", "kind": "code", "reason": "An entry point."},
        ]
        out = _dedupe_tour_steps(tour)
        assert [s["title"] for s in out] == ["a.ts", "d.py"]

    def test_keeps_distinct_reasons(self):
        tour = [
            {"title": "a", "kind": "code", "reason": "The API layer's anchor."},
            {"title": "b", "kind": "code", "reason": "The UI layer's anchor."},
        ]
        assert len(_dedupe_tour_steps(tour)) == 2

    def test_non_consecutive_reoccurrence_survives(self):
        tour = [
            {"title": "a", "kind": "code", "reason": "hub"},
            {"title": "b", "kind": "overview", "reason": "start"},
            {"title": "c", "kind": "code", "reason": "hub"},
        ]
        assert len(_dedupe_tour_steps(tour)) == 3

    def test_empty(self):
        assert _dedupe_tour_steps([]) == []

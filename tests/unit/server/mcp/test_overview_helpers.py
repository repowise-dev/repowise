"""Unit tests for get_overview's pure rendering helpers."""

from __future__ import annotations

import json

from repowise.server.mcp_server.tool_overview import (
    _compact_overview_content,
    _dedupe_tour_steps,
)


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


class TestBuildGuidedTourOrder:
    def test_order_is_contiguous_after_dedupe(self):
        from types import SimpleNamespace

        from repowise.server.mcp_server.tool_overview import _build_guided_tour

        tour = [
            {"order": 1, "title": "a.py", "kind": "code", "reason": "entry"},
            {"order": 2, "title": "b.py", "kind": "code", "reason": "hub"},
            {"order": 3, "title": "c.py", "kind": "code", "reason": "hub"},
            {"order": 4, "title": "d.py", "kind": "code", "reason": "infra"},
        ]
        page = SimpleNamespace(metadata_json=json.dumps({"guided_tour": tour}))
        result: dict = {}
        _build_guided_tour(page, result, {}, want_tour=True)

        orders = [step["order"] for step in result["guided_tour"]]
        assert orders == list(range(1, len(orders) + 1))

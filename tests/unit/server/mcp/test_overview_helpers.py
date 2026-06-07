"""Unit tests for get_overview's pure rendering helpers."""

from __future__ import annotations

from repowise.server.mcp_server.tool_overview import (
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

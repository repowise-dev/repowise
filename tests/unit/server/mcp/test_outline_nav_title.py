"""The overview outline lists pages by their sidebar names."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server.tool_overview.outline import _build_outline


def _row(pid, page_type, title, parent=None, section=None, metadata_json=None):
    return SimpleNamespace(
        id=pid,
        page_type=page_type,
        title=title,
        target_path=pid.split(":", 1)[1],
        parent_page_id=parent,
        display_order=1,
        section_number=section,
        metadata_json=metadata_json,
    )


def test_outline_titles_are_the_short_names():
    rows = [
        _row("repo_overview:demo", "repo_overview", "Repository Overview: demo"),
        _row("module_page:pkg/cli", "module_page", "Cli Overview", "repo_overview:demo", "1"),
        _row(
            "module_page:pkg/core",
            "module_page",
            "Core",
            "repo_overview:demo",
            "2",
            '{"nav_title": "Indexing Engine"}',
        ),
    ]
    outline = _build_outline(rows, 1, OmissionCollector("get_overview"))
    assert [s["title"] for s in outline["sections"]] == ["CLI", "Indexing Engine"]

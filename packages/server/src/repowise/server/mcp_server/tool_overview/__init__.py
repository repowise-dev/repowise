"""MCP Tool 1: get_overview, the repository architecture overview.

One module per payload block; the root registers the tool and re-exports the
names tests read. Monkeypatch collaborators on the module that looks them up.

The re-exports bind on first attribute access, not at import. Importing a leaf
such as ``tool_overview.actions`` initialises this package first, and eager
re-exports made that leaf cost every block module plus the tool and its
persistence chain. ``from ... import get_overview`` still loads and registers
the tool; ``ensure_full_surface`` reads that name for the same reason."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from repowise.server.mcp_server.tool_overview.decisions import _build_key_decisions
    from repowise.server.mcp_server.tool_overview.graph import (
        _build_community_summary,
        _community_display_label,
    )
    from repowise.server.mcp_server.tool_overview.health import _build_code_health
    from repowise.server.mcp_server.tool_overview.history import (
        _build_git_health,
        _build_knowledge_map,
        _owner_display_name,
    )
    from repowise.server.mcp_server.tool_overview.outline import _build_outline, _outline_index
    from repowise.server.mcp_server.tool_overview.pages import (
        _MODULE_CAP,
        _capped_entry_points,
        _compact_overview_content,
        _module_order_key,
        _resolve_title,
        _section_sort_key,
    )
    from repowise.server.mcp_server.tool_overview.surface import _tool_surface_guide
    from repowise.server.mcp_server.tool_overview.tool import get_overview
    from repowise.server.mcp_server.tool_overview.tour import (
        _build_guided_tour,
        _dedupe_tour_steps,
    )
    from repowise.server.mcp_server.tool_overview.workspace import _build_workspace_footer

__getattr__, __dir__ = lazy_exports(
    __name__,
    {
        "_MODULE_CAP": ".pages",
        "_build_code_health": ".health",
        "_build_community_summary": ".graph",
        "_build_git_health": ".history",
        "_build_guided_tour": ".tour",
        "_build_key_decisions": ".decisions",
        "_build_knowledge_map": ".history",
        "_build_outline": ".outline",
        "_build_workspace_footer": ".workspace",
        "_capped_entry_points": ".pages",
        "_community_display_label": ".graph",
        "_compact_overview_content": ".pages",
        "_dedupe_tour_steps": ".tour",
        "_module_order_key": ".pages",
        "_outline_index": ".outline",
        "_owner_display_name": ".history",
        "_resolve_title": ".pages",
        "_section_sort_key": ".pages",
        "_tool_surface_guide": ".surface",
        "get_overview": ".tool",
    },
    globals(),
)

__all__ = [
    "_MODULE_CAP",
    "_build_code_health",
    "_build_community_summary",
    "_build_git_health",
    "_build_guided_tour",
    "_build_key_decisions",
    "_build_knowledge_map",
    "_build_outline",
    "_build_workspace_footer",
    "_capped_entry_points",
    "_community_display_label",
    "_compact_overview_content",
    "_dedupe_tour_steps",
    "_module_order_key",
    "_outline_index",
    "_owner_display_name",
    "_resolve_title",
    "_section_sort_key",
    "_tool_surface_guide",
    "get_overview",
]

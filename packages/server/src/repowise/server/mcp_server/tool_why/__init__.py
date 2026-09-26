"""MCP Tool 4: get_why, intent archaeology and decision search.

One module per mode over shared layers; importing the package registers the
tool and re-exports the names tests read. Monkeypatch a collaborator on the
module that looks it up (e.g. ``path_mode.describe_decision_currency``), not here.
"""

from __future__ import annotations

from repowise.server.mcp_server.tool_why.caps import (
    _MAX_AFFECTED_FILES,
    _MAX_HEALTH_PROPOSED,
    _MAX_HEALTH_STALE,
    _MAX_HEALTH_UNGOVERNED,
    _MAX_PATH_DECISIONS,
    _MAX_SEARCH_DECISIONS,
    _fit_path_response,
)
from repowise.server.mcp_server.tool_why.ranking import _rank_keyword_matches
from repowise.server.mcp_server.tool_why.tool import get_why

__all__ = [
    "_MAX_AFFECTED_FILES",
    "_MAX_HEALTH_PROPOSED",
    "_MAX_HEALTH_STALE",
    "_MAX_HEALTH_UNGOVERNED",
    "_MAX_PATH_DECISIONS",
    "_MAX_SEARCH_DECISIONS",
    "_fit_path_response",
    "_rank_keyword_matches",
    "get_why",
]

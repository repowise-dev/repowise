"""MCP Tool 4: get_why — intent archaeology and decision search.

Facade: the tool used to be a single ``tool_why.py``. It is now a package with
one module per mode (``path_mode``, ``search``, ``workspace``, ``reference``,
``dashboard``) over shared layers (``loading`` reads, ``ranking`` scores,
``projection`` shapes rows, ``caps`` bounds them, ``archaeology`` falls back to
git, ``basis`` names the lane an answer rests on). Importing the package
registers the tool, and the names tests read stay importable from here.

Monkeypatch a collaborator on the module that looks it up, not on this facade:
``path_mode.describe_decision_currency``, ``search.episode_evidence``,
``archaeology._run_git_log`` and so on.
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

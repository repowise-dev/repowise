"""MCP tool: get_health — code-health markers and per-file scores.

``tool.py`` holds the registered tool; the sibling modules hold the pieces it
composes. The private names below are re-exported because other tools and the
tests import them from ``tool_health`` directly.
"""

from __future__ import annotations

from repowise.core.analysis.health.aggregation import module_rollups as _module_rollups
from repowise.server.mcp_server.tool_health.coverage import (
    _attach_coverage_decay,
    _serialize_coverage_row,
)
from repowise.server.mcp_server.tool_health.plans import _validation_profile
from repowise.server.mcp_server.tool_health.serialize import (
    _health_finding_id,
    _perf_rank,
    _rank_emitted,
    _refactoring_plan_id,
    _serialize_refactoring,
)
from repowise.server.mcp_server.tool_health.tool import _ONLY_ALIASES, get_health

__all__ = [
    "_ONLY_ALIASES",
    "_attach_coverage_decay",
    "_health_finding_id",
    "_module_rollups",
    "_perf_rank",
    "_rank_emitted",
    "_refactoring_plan_id",
    "_serialize_coverage_row",
    "_serialize_refactoring",
    "_validation_profile",
    "get_health",
]

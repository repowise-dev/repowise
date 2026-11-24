"""Stable names for a host that serves the MCP tools inside its own process.

Wrap a tool with :func:`tool_middleware`, then call it inside
``with serving(ctx):`` where *ctx* is a ``RepoContext``. Pass
``telemetry=False`` when the host does its own usage accounting.
"""

from __future__ import annotations

from repowise.server.mcp_server import tool_middleware
from repowise.server.mcp_server._state import request_context, serving

__all__ = ["request_context", "serving", "tool_middleware"]

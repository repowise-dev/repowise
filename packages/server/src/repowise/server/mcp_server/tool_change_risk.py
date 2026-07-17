"""MCP tool for live commit and range change-risk scoring."""

from __future__ import annotations

import asyncio
import subprocess

from repowise.core.analysis.change_risk import change_risk_payload, score_live_change
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import _resolve_repo_context


@mcp.tool()
async def get_change_risk(
    revspec: str = "HEAD",
    repo: str | None = None,
    extensions: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    baseline: int = 200,
) -> dict:
    """Score a live commit or ``base..head`` range from its diff shape.

    Use this for a pre-merge score of a commit or PR range. It is distinct from
    ``get_risk``, which assesses indexed files and PR blast radius. ``extensions``
    restricts counted suffixes; ``exclude_patterns`` omits gitignore-style paths.
    Both filters also apply to the baseline used for the repository percentile.

    Args:
        revspec: Commit or ``base..head`` range to score. Defaults to ``HEAD``.
        repo: Repository alias in workspace mode; omit for the default repository.
        extensions: File suffixes to count, for example ``[".py", ".ts"]``.
        exclude_patterns: Gitignore-style paths to omit, for example ``["tests/", "*.md"]``.
        baseline: Recent commits to sample for percentile ranking; 0 disables it.
    """
    ctx = await _resolve_repo_context(repo)
    try:
        result = await asyncio.to_thread(
            score_live_change,
            ctx.path,
            revspec,
            extensions=tuple(extensions or ()),
            exclude_patterns=tuple(exclude_patterns or ()),
            baseline=baseline,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    except subprocess.CalledProcessError as exc:
        return {"error": f"Could not read change {revspec!r}: {exc.stderr or exc}"}
    return change_risk_payload(result)

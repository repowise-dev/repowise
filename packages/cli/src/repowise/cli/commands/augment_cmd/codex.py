"""Codex lifecycle hooks: SessionStart/UserPromptSubmit + post-edit staleness.

SessionStart delivers a short MCP-usage context block plus the same
relevance-ranked standing-decisions block that Claude Code receives
(see :mod:`.decision_inject`), so decisions follow the user across agents.
"""

from __future__ import annotations

from pathlib import Path

from ._shared import _find_repo_root
from .decision_inject import _session_decision_block

_MCP_CONTEXT = (
    "[repowise] This repository has a local codebase wiki and graph index. "
    "Use the repowise MCP tools for architecture overview, semantic search, "
    "implementation context, risk/hotspot checks, decision history, and "
    "dead-code analysis. After meaningful edits or git operations, run "
    "`repowise update` when refreshed context is needed."
)


def _handle_codex_context_event(
    event: str, cwd: str, session_id: str = ""
) -> str | None:
    """Return Codex developer context + standing decisions on SessionStart."""
    if event not in ("SessionStart", "UserPromptSubmit"):
        return None

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    if event == "UserPromptSubmit":
        return _MCP_CONTEXT

    # SessionStart: base context + relevance-ranked standing decisions.
    try:
        decisions = _session_decision_block(repo_path, session_id)
    except Exception:
        decisions = None
    return f"{_MCP_CONTEXT}\n{decisions}" if decisions else _MCP_CONTEXT


def _handle_post_edit_use(cwd: str) -> str | None:
    """After a Codex edit tool completes, flag that indexed context may be stale."""
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.exists():
        return None

    return (
        "[repowise] Files were edited after the last indexed snapshot. "
        "Run `repowise update` before relying on refreshed docs, graph context, "
        "risk checks, or dead-code results."
    )

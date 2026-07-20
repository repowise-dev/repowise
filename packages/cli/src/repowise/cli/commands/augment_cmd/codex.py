"""Codex lifecycle hooks: SessionStart/UserPromptSubmit + post-edit staleness.

SessionStart delivers a short MCP-usage context block plus the same
relevance-ranked standing-decisions block that Claude Code receives
(see :mod:`.decision_inject`), so decisions follow the user across agents.
"""

from __future__ import annotations

from pathlib import Path

from ._shared import _find_repo_root
from .decision_inject import (
    _edit_decision_notice,
    _edit_fix_history_notice,
    _session_decision_block,
)
from .read_state import _load_session_state, _save_session_state

_MCP_CONTEXT = (
    "[repowise] This repository has a local codebase wiki and graph index. "
    "Use the repowise MCP tools for architecture overview, semantic search, "
    "implementation context, risk/hotspot checks, decision history, and "
    "dead-code analysis. After meaningful edits or git operations, run "
    "`repowise update` when refreshed context is needed."
)


def _handle_codex_context_event(event: str, cwd: str, session_id: str = "") -> str | None:
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


def _handle_post_edit_use(
    cwd: str,
    *,
    session_id: str = "",
    tool_input: dict | None = None,
) -> str | None:
    """After a Codex edit tool completes, flag that indexed context may be stale
    and surface the edited file's governing decision, if any.
    """
    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.exists():
        return None

    staleness = (
        "[repowise] Files were edited after the last indexed snapshot. "
        "Run `repowise update` before relying on refreshed docs, graph context, "
        "risk checks, or dead-code results."
    )

    # Governing-decision and bug-history notices, from the same builders Claude
    # Code's edit-time path uses, under the same per-session caps.
    notices: list[str] = []
    if tool_input is not None and session_id:
        file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        if isinstance(file_path, str) and file_path.strip():
            from .read_state import _relativize

            rel = _relativize(file_path, repo_path)
            if rel is not None:
                state = _load_session_state(repo_path, session_id)
                for emit in (
                    lambda: _edit_decision_notice(repo_path, rel, session_id, state),
                    lambda: _edit_fix_history_notice(repo_path, rel, session_id),
                ):
                    try:
                        line = emit()
                    except Exception:
                        line = None
                    if line:
                        notices.append(line)
                _save_session_state(repo_path, state)

    return "\n".join([staleness, *notices]) if notices else staleness

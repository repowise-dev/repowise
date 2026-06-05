"""Claude Code adapter — PreToolUse payloads and ``hookSpecificOutput`` responses.

Protocol reference (Claude Code hooks): a PreToolUse hook receives a JSON
payload on stdin with ``hook_event_name``/``tool_name``/``tool_input``/``cwd``
(snake_case) and may answer with camelCase ``hookSpecificOutput`` JSON on
stdout. ``updatedInput`` replaces only the fields it names; with
``permissionDecision: "ask"`` the user is shown the *modified* command for
approval, which is exactly the posture we want for a rewritten command.
"""

from __future__ import annotations

import json
import os.path
from typing import TYPE_CHECKING, ClassVar

from repowise.cli.agent_adapters.base import AgentAdapter, RewriteRequest, RewriteResult

if TYPE_CHECKING:
    from pathlib import Path


class ClaudeCodeAdapter(AgentAdapter):
    name: ClassVar[str] = "claude-code"

    def detect(self) -> bool:
        return os.path.isdir(os.path.expanduser("~/.claude"))

    def parse_hook_payload(self, raw: str) -> RewriteRequest | None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("hook_event_name") != "PreToolUse":
            return None
        if payload.get("tool_name") != "Bash":
            return None
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return None
        cwd = payload.get("cwd")
        return RewriteRequest(command=command, cwd=cwd if isinstance(cwd, str) else "")

    def render_response(self, result: RewriteResult) -> str:
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": result.permission,
                    "permissionDecisionReason": result.reason,
                    "updatedInput": {"command": result.command},
                }
            }
        )

    def install_rewrite_hook(self) -> Path | None:
        from repowise.cli.editor_integrations.claude_config import (
            install_claude_code_rewrite_hook,
        )

        return install_claude_code_rewrite_hook()

    def uninstall_rewrite_hook(self) -> bool:
        from repowise.cli.editor_integrations.claude_config import (
            uninstall_claude_code_rewrite_hook,
        )

        return uninstall_claude_code_rewrite_hook()

    def rewrite_hook_installed(self) -> bool:
        from repowise.cli.editor_integrations.claude_config import (
            claude_code_rewrite_hook_installed,
        )

        return claude_code_rewrite_hook_installed()

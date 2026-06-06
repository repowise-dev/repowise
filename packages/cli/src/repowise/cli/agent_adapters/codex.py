"""Codex CLI adapter — PreToolUse payloads and ``hookSpecificOutput`` responses.

Protocol reference (Codex hooks, developers.openai.com/codex/hooks): hooks
load from ``~/.codex/hooks.json`` (or a repo-local ``.codex/hooks.json``); a
PreToolUse hook receives JSON on stdin with snake_case
``hook_event_name``/``tool_name``/``tool_input``/``cwd`` — the shell tool is
named ``Bash`` and ``tool_input.command`` is a string — and answers with
camelCase ``hookSpecificOutput`` JSON on stdout.

Two protocol limits shape this adapter's honesty posture:

  - ``permissionDecision`` supports only ``"allow"`` and ``"deny"`` —
    ``"ask"`` is parsed but **not honored**, and ``updatedInput`` is only
    applied with ``"allow"``. There is no ask-with-mutation primitive, so
    rewrites fire **only** for command families the user explicitly set to
    ``permission: allow``; ``ask`` families pass through untouched
    (:attr:`rewrite_permissions`). A silently mutated command would be a
    permission escalation, and we don't do that.
  - ``updatedInput`` rewriting requires Codex >= 0.137 (older builds reject
    it at runtime as unsupported). Install is version-gated in
    :mod:`repowise.cli.editor_integrations.codex_config`.
"""

from __future__ import annotations

import json
import os.path
from typing import TYPE_CHECKING, ClassVar

from repowise.cli.agent_adapters.base import AgentAdapter, RewriteRequest, RewriteResult

if TYPE_CHECKING:
    from pathlib import Path


class CodexAdapter(AgentAdapter):
    name: ClassVar[str] = "codex"

    #: No ask-with-mutation in the Codex hook protocol — see module docstring.
    rewrite_permissions: ClassVar[frozenset[str]] = frozenset({"allow"})

    def detect(self) -> bool:
        return os.path.isdir(os.path.expanduser("~/.codex"))

    def parse_hook_payload(self, raw: str) -> RewriteRequest | None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("hook_event_name") != "PreToolUse":
            return None
        # Codex's shell tool is named "Bash" (there is no separate
        # PowerShell tool); the command is always a string.
        if payload.get("tool_name") != "Bash":
            return None
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return None
        cwd = payload.get("cwd")
        return RewriteRequest(
            command=command,
            cwd=cwd if isinstance(cwd, str) else "",
            shell="posix",
        )

    def render_response(self, result: RewriteResult) -> str:
        # Only called for permissions in ``rewrite_permissions`` — i.e.
        # "allow". Rendering "ask" here would make Codex error the hook.
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
        from repowise.cli.editor_integrations.codex_config import (
            install_codex_rewrite_hook,
        )

        return install_codex_rewrite_hook()

    def uninstall_rewrite_hook(self) -> bool:
        from repowise.cli.editor_integrations.codex_config import (
            uninstall_codex_rewrite_hook,
        )

        return uninstall_codex_rewrite_hook()

    def rewrite_hook_installed(self) -> bool:
        from repowise.cli.editor_integrations.codex_config import (
            codex_rewrite_hook_installed,
        )

        return codex_rewrite_hook_installed()

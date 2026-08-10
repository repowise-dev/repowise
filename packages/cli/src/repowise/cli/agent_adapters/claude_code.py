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
import os
import os.path
from typing import TYPE_CHECKING, ClassVar

from repowise.cli.agent_adapters.base import AgentAdapter, RewriteRequest, RewriteResult

if TYPE_CHECKING:
    from pathlib import Path


#: Every name Claude Code gives the tool that carries a shell command string.
#: ``PowerShell`` is the Windows one and has the same payload shape. Kept as a
#: set, and the installed matcher is derived from it below, for the reason the
#: Codex adapter states at length: a rename upstream is silent here, because a
#: gate that stops matching looks exactly like a hook with nothing to say.
SHELL_TOOL_NAMES: frozenset[str] = frozenset({"Bash", "PowerShell"})

#: The same set as a settings.json matcher. Derived rather than written twice.
SHELL_TOOL_MATCHER: str = "|".join(sorted(SHELL_TOOL_NAMES))


#: Claude Code release that introduced ``updatedToolOutput``. Older clients
#: ignore the field, which would silently drop the enrichment, so on those a
#: replacing surface leaves the tool result untouched.
MIN_UPDATED_OUTPUT_VERSION: tuple[int, ...] = (2, 1, 218)


class ClaudeCodeAdapter(AgentAdapter):
    name: ClassVar[str] = "claude-code"

    shell_tool_names: ClassVar[frozenset[str]] = SHELL_TOOL_NAMES
    read_tool_names: ClassVar[frozenset[str]] = frozenset({"Read"})
    edit_tool_names: ClassVar[frozenset[str]] = frozenset({"Edit", "Write"})
    search_tool_names: ClassVar[frozenset[str]] = frozenset({"Grep", "Glob"})

    #: The only harness observed so far whose PostToolUse protocol can replace
    #: a tool result (``updatedToolOutput``).
    replaces_tool_output: ClassVar[bool] = True

    def detect(self) -> bool:
        return os.path.isdir(os.path.expanduser("~/.claude"))

    def supports_updated_output(self) -> bool:
        """True when the installed Claude Code build can honour a replacement.

        There is no version in the hook payload and no version env var, and
        ``claude --version`` is a ~1s subprocess this path cannot afford. The
        one cheap on-disk signal is the updater's own record, so we read that
        and **fail open** — an unsupported client ignores the unknown field,
        which costs a skipped enrichment, never a broken tool call.

        ``REPOWISE_HOOK_UPDATED_OUTPUT`` overrides, for a one-session A/B.
        """
        override = os.environ.get("REPOWISE_HOOK_UPDATED_OUTPUT")
        if override is not None:
            return override.strip().lower() in ("1", "true", "yes", "on")
        version = _recorded_client_version()
        return version is None or version >= MIN_UPDATED_OUTPUT_VERSION

    def parse_hook_payload(self, raw: str) -> RewriteRequest | None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("hook_event_name") != "PreToolUse":
            return None
        tool_name = payload.get("tool_name")
        if tool_name not in SHELL_TOOL_NAMES:
            return None
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return None
        cwd = payload.get("cwd")
        session_id = payload.get("session_id")
        return RewriteRequest(
            command=command,
            cwd=cwd if isinstance(cwd, str) else "",
            shell="powershell" if tool_name == "PowerShell" else "posix",
            session_id=session_id if isinstance(session_id, str) else "",
        )

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

    def rewrite_hook_matcher(self) -> str | None:
        from repowise.cli.editor_integrations.claude_config import (
            claude_code_rewrite_hook_matcher,
        )

        return claude_code_rewrite_hook_matcher()


def _recorded_client_version() -> tuple[int, ...] | None:
    """Installed Claude Code version per ``~/.claude/.last-update-result.json``.

    Best effort by construction: the file only exists once the updater has run
    at least once, and its ``version_to`` is null on a failed update — in which
    case ``version_from`` is what is still installed.

    ``json`` is already imported by this module's payload parsing, so this
    costs a single small file read on the one path that asks for it.
    """
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", ".last-update-result.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RuntimeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("version_to", "version_from"):
        value = data.get(key)
        if isinstance(value, str) and value:
            parts = value.split(".")
            if all(p.isdigit() for p in parts) and parts:
                return tuple(int(p) for p in parts)
    return None

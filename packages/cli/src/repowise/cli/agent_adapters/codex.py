"""Codex CLI adapter — PreToolUse payloads and ``hookSpecificOutput`` responses.

Protocol reference (Codex hooks, developers.openai.com/codex/hooks): hooks
load from ``~/.codex/hooks.json`` (or a repo-local ``.codex/hooks.json``); a
PreToolUse hook receives JSON on stdin with snake_case
``hook_event_name``/``tool_name``/``tool_input``/``cwd`` — the shell tool
answers to several names (:data:`SHELL_TOOL_NAMES`) and ``tool_input.command``
is a string — and answers with camelCase ``hookSpecificOutput`` JSON on stdout.

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
import os
from typing import TYPE_CHECKING, ClassVar

from repowise.cli.agent_adapters.base import AgentAdapter, RewriteRequest, RewriteResult

if TYPE_CHECKING:
    from pathlib import Path


#: Every name Codex has given the tool that carries a shell command *string*,
#: because it has given it more than one and the rewrite is dead for any name
#: it does not answer to.
#:
#: This started as the single literal ``"Bash"``. Measured against 18 real
#: rollouts from Codex 0.145: ``"Bash"`` does not appear once, and the shell
#: calls are ``shell_command`` (322), whose ``tool_input.command`` is a string
#: — the shape :meth:`CodexAdapter.parse_hook_payload` already reads. A rename
#: upstream is silent here (the gate stops matching and the hook never fires),
#: so this is a set and new names are added rather than swapped in.
#:
#: **``exec`` is deliberately not in it.** It looks like a shell tool in the
#: rollouts (423 calls) and is not one: it is a ``custom_tool_call`` whose
#: input is a *JavaScript program* that calls ``tools.shell_command(...)``
#: internally. There is no command string to read and nothing to rewrite, so
#: matching it would buy 423 hook subprocesses that can only decline. A name
#: belongs here once it is observed carrying ``tool_input.command``.
SHELL_TOOL_NAMES: frozenset[str] = frozenset({"Bash", "shell_command"})

#: The same set as a hooks.json matcher. Derived rather than written twice —
#: an installer whose matcher disagrees with the adapter's gate installs a
#: hook that runs and then declines every payload it is handed.
SHELL_TOOL_MATCHER: str = "|".join(sorted(SHELL_TOOL_NAMES))


class CodexAdapter(AgentAdapter):
    name: ClassVar[str] = "codex"

    shell_tool_names: ClassVar[frozenset[str]] = SHELL_TOOL_NAMES

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
        # Codex has no separate PowerShell tool, but it has renamed the shell
        # one; see SHELL_TOOL_NAMES. The command is a string on every name
        # that reaches the check below.
        if payload.get("tool_name") not in SHELL_TOOL_NAMES:
            return None
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return None
        cwd = payload.get("cwd")
        return RewriteRequest(
            command=command,
            cwd=cwd if isinstance(cwd, str) else "",
            # Codex names its shell tool the same on every platform, so the
            # dialect has to come from the platform. It is PowerShell on
            # Windows — the real rollouts are full of `get-content`,
            # `get-childitem` and `$env:` — and calling that "posix" skips
            # `decide`'s PowerShell-alias bailout, which exists precisely to
            # stop an alias command being re-run through cmd.exe. Dead code
            # until the gate above started matching; live now.
            shell="powershell" if os.name == "nt" else "posix",
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

    def rewrite_hook_matcher(self) -> str | None:
        from repowise.cli.editor_integrations.codex_config import (
            codex_rewrite_hook_matcher,
        )

        return codex_rewrite_hook_matcher()

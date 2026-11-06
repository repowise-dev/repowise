"""Pi adapter — PreToolUse payloads and ``hookSpecificOutput`` responses.

Protocol reference (Pi extensions, pi.dev/docs/latest/extensions): Pi's
built-in tools are lowercase (``bash``, ``read``, ``edit``, ``write``,
``grep``, ``find``, ``ls``) and MCP tools arrive as
``mcp__<server>__<tool>``. The Repowise Pi extension
(``plugins/pi/repowise.ts``) calls ``repowise-rewrite --agent pi`` from its
``tool_call`` handler with a PreToolUse-shaped payload, and
``repowise-augment --client pi`` from ``tool_result`` /
``before_agent_start`` with a Pi-shaped PostToolUse payload that
:mod:`repowise.cli.commands.augment_cmd.pi` normalises before dispatch.

Two protocol facts shape this adapter's honesty posture:

  - Pi's ``tool_call`` handler mutates ``event.input`` directly, so there
    is no ask-with-mutation primitive: a rewrite the config marks ``ask``
    passes through untouched rather than being silently applied
    (:attr:`rewrite_permissions`). The extension applies an ``allow``
    rewrite by assigning the returned command to ``event.input.command``.
  - Pi's ``tool_result`` handlers *can* return a content patch, so the
    replacing surfaces (read skeleton, grep flood digest) apply here —
    the extension returns the ``hookSpecificOutput.updatedToolOutput`` it
    receives as the new result content.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING, ClassVar

from repowise.cli.agent_adapters.base import (
    SHELL_POSIX,
    SHELL_POWERSHELL,
    AgentAdapter,
    RewriteRequest,
    RewriteResult,
)

if TYPE_CHECKING:
    from pathlib import Path

#: Every name Pi gives the tool that carries a shell command string.
#: ``powershell`` is the Windows one and has the same payload shape.
#: Kept as a set for the reason the Codex adapter states at length: a
#: rename upstream is silent here, because a gate that stops matching looks
#: exactly like a hook with nothing to say.
SHELL_TOOL_NAMES: frozenset[str] = frozenset({"bash", "powershell"})

#: The same set as the extension's ``tool_call`` gate. Derived rather than
#: written twice — an extension whose gate disagrees with the adapter's
#: installs a hook that runs and then declines every payload it is handed.
#: Kept in step with ``plugins/pi/repowise.ts`` by
#: ``tests/unit/cli/test_pi_plugin.py``.
SHELL_TOOL_MATCHER: str = "|".join(sorted(SHELL_TOOL_NAMES))

#: Pi's file tools, in the extension's spelling. ``find`` lists files by
#: pattern, which is the Glob surface's shape, not Grep's — see
#: :mod:`repowise.cli.commands.augment_cmd.pi`.
READ_TOOL_NAMES: frozenset[str] = frozenset({"read"})
EDIT_TOOL_NAMES: frozenset[str] = frozenset({"edit", "write"})
SEARCH_TOOL_NAMES: frozenset[str] = frozenset({"grep", "find"})

#: The extension file, as installed by the user or a project. The adapter
#: probes for it; it does not write it — see :meth:`PiAdapter.install_rewrite_hook`.
EXTENSION_FILENAME = "repowise.ts"


def _user_extensions_dir() -> Path:
    """Pi's user extensions directory, following its environment override.

    Mirrors the Pi target's ``config_dir`` (``PI_CODING_AGENT_DIR`` or
    ``~/.pi/agent``); the two must agree or the probe below looks in a
    place the target never writes to.
    """
    from pathlib import Path

    configured = os.environ.get("PI_CODING_AGENT_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"
    return base / "extensions"


def extension_path() -> Path:
    """Where the Repowise extension lives when installed for this user."""
    return _user_extensions_dir() / EXTENSION_FILENAME


class PiAdapter(AgentAdapter):
    name: ClassVar[str] = "pi"

    #: Pi rewrites are tagged as their own surface in the savings ledger.
    savings_source: ClassVar[str | None] = "hook-pi"

    shell_tool_names: ClassVar[frozenset[str]] = SHELL_TOOL_NAMES
    read_tool_names: ClassVar[frozenset[str]] = READ_TOOL_NAMES
    edit_tool_names: ClassVar[frozenset[str]] = EDIT_TOOL_NAMES
    search_tool_names: ClassVar[frozenset[str]] = SEARCH_TOOL_NAMES

    #: No ask-with-mutation in Pi's tool_call contract — see module docstring.
    rewrite_permissions: ClassVar[frozenset[str]] = frozenset({"allow"})

    #: Pi's tool_result handlers can return a content patch, so a replacing
    #: surface may hand this adapter one (inherited default spelled out
    #: because it is load-bearing: the read-skeleton and flood-digest
    #: surfaces only serve when this is True).
    replaces_tool_output: ClassVar[bool] = True

    def detect(self) -> bool:
        return shutil.which("pi") is not None or os.path.isdir(
            os.path.expanduser("~/.pi/agent")
        )

    def supports_updated_output(self) -> bool:
        """True: a Pi ``tool_result`` handler can patch the result content.

        There is no version to probe — the extension itself applies the
        patch, and when the extension is absent ``repowise-augment`` never
        runs at all, so there is no older client to fail open for.
        """
        return True

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
            shell=SHELL_POWERSHELL if tool_name == "powershell" else SHELL_POSIX,
            session_id=session_id if isinstance(session_id, str) else "",
        )

    def render_response(self, result: RewriteResult) -> str:
        # Deliberately the Claude Code shape: the Pi extension reads
        # ``hookSpecificOutput.updatedInput.command`` out of it and assigns
        # that to ``event.input.command``. One response shape for both
        # harnesses means one parser in the extension and one shape in the
        # tests; the extension, not the CLI, owns the Pi-side translation.
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
        """The Pi extension is installed by copying a file, not by writing a
        config entry — see ``plugins/pi/README.md``. That copy belongs to
        the agent-target installer (the Pi target's scope), not to the
        rewrite adapter, so this returns None rather than writing a file a
        second installer would then fight over.
        """
        return None

    def uninstall_rewrite_hook(self) -> bool:
        """Remove the user-level extension file, if it is there."""
        path = extension_path()
        try:
            if path.is_file():
                path.unlink()
                return True
        except OSError:
            return False
        return False

    def rewrite_hook_installed(self) -> bool:
        return extension_path().is_file()

    def rewrite_hook_matcher(self) -> str | None:
        # Pi has no matcher concept: the extension gates on tool names in
        # its own ``tool_call`` handler. Returning None keeps the presence
        # answer, which is what this method refines and is never worse than.
        return None

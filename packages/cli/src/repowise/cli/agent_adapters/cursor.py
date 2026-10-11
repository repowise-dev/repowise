"""Cursor adapter: the distill rewrite on ``preToolUse`` in ``~/.cursor/hooks.json``.

Host quirks, all from https://cursor.com/docs/agent/hooks:

- Only ``preToolUse`` documents ``updated_input``; ``beforeShellExecution`` cannot rewrite.
- ``preToolUse`` does not enforce ``ask``, so only ``allow`` families rewrite.
- ``updated_input`` is the input "to use instead", so the whole input is echoed back.
- ``updated_mcp_tool_output`` is MCP-only, so no shell or file result is replaced.
- Entries are flat ``{command, matcher, timeout}`` under ``{"version": 1, "hooks": ...}``.
"""

from __future__ import annotations

import json
import os
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

#: Cursor's one shell tool name in ``preToolUse`` payloads.
SHELL_TOOL_NAMES: frozenset[str] = frozenset({"Shell"})

#: The installed matcher, derived so it cannot disagree with the gate.
SHELL_TOOL_MATCHER: str = "|".join(sorted(SHELL_TOOL_NAMES))

_REWRITE_HOOK_COMMAND = "repowise-rewrite"
_EVENT = "preToolUse"

#: Bare command on PATH, as Claude Code's and Codex's entries are.
_REWRITE_HOOK_ENTRY = {
    "command": f"{_REWRITE_HOOK_COMMAND} --agent cursor",
    "matcher": SHELL_TOOL_MATCHER,
    "timeout": 5,
}


class CursorAdapter(AgentAdapter):
    name: ClassVar[str] = "cursor"

    savings_source: ClassVar[str | None] = "hook-cursor"

    shell_tool_names: ClassVar[frozenset[str]] = SHELL_TOOL_NAMES

    rewrite_permissions: ClassVar[frozenset[str]] = frozenset({"allow"})

    replaces_tool_output: ClassVar[bool] = False

    def __init__(self) -> None:
        # Echoed back whole by render_response; one payload per hook process.
        self._tool_input: dict = {}

    def detect(self) -> bool:
        return os.path.isdir(os.path.expanduser("~/.cursor"))

    def parse_hook_payload(self, raw: str) -> RewriteRequest | None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("hook_event_name") != _EVENT:
            return None
        if payload.get("tool_name") not in SHELL_TOOL_NAMES:
            return None
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return None
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        self._tool_input = tool_input
        cwd = next(
            (
                value
                for value in (
                    tool_input.get("working_directory"),
                    payload.get("cwd"),
                    *(payload.get("workspace_roots") or [])[:1],
                )
                if isinstance(value, str) and value
            ),
            "",
        )
        session_id = payload.get("conversation_id")
        return RewriteRequest(
            command=command,
            cwd=cwd,
            session_id=session_id if isinstance(session_id, str) else "",
            # The payload names no shell; PowerShell's extra bailouts are the safe side.
            shell=SHELL_POWERSHELL if os.name == "nt" else SHELL_POSIX,
        )

    def render_response(self, result: RewriteResult) -> str:
        # Only called for "allow" (rewrite_permissions); "ask" is not enforced.
        return json.dumps(
            {
                "permission": result.permission,
                "updated_input": {**self._tool_input, "command": result.command},
            }
        )

    def install_rewrite_hook(self) -> Path | None:
        return install_cursor_rewrite_hook()

    def uninstall_rewrite_hook(self) -> bool:
        return uninstall_cursor_rewrite_hook()

    def rewrite_hook_installed(self) -> bool:
        return cursor_rewrite_hook_matcher() is not None

    def rewrite_hook_matcher(self) -> str | None:
        return cursor_rewrite_hook_matcher()


# ---------------------------------------------------------------------------
# ~/.cursor/hooks.json: additive, idempotent, other entries untouched.
# ---------------------------------------------------------------------------


def user_hooks_path() -> Path:
    from pathlib import Path  # lazy: pathlib is too slow for the hook hot path

    return Path.home() / ".cursor" / "hooks.json"


def _is_rewrite_hook(entry: object) -> bool:
    """Ours when the command's executable basename is ``repowise-rewrite``.

    A substring test would claim a user's wrapper that merely mentions it.
    """
    if not isinstance(entry, dict):
        return False
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    command = command.strip()
    quoted = command[0] in "\"'"
    exe = command[1:].split(command[0], 1)[0] if quoted else command.split(None, 1)[0]
    name = exe.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.removesuffix(".exe") == _REWRITE_HOOK_COMMAND


def _load(path: Path) -> dict:
    from repowise.cli.mcp_config import load_existing_config

    return load_existing_config(path)


def _write(path: Path, doc: dict) -> None:
    from repowise.cli.agent_targets.formats.json_merge import write_json_config

    write_json_config(path, doc)


def install_cursor_rewrite_hook() -> Path | None:
    """Register the rewrite entry under ``preToolUse``; the path, or None on failure."""
    path = user_hooks_path()
    try:
        if path.exists():
            doc = _load(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            doc = {}
        hooks = doc.setdefault("hooks", {})
        entries = hooks.setdefault(_EVENT, [])
        if not any(_is_rewrite_hook(entry) for entry in entries):
            doc.setdefault("version", 1)
            entries.append(dict(_REWRITE_HOOK_ENTRY))
            _write(path, doc)
        return path
    except Exception:
        # A malformed file or a failed write: report failure, never raise.
        return None


def uninstall_cursor_rewrite_hook() -> bool:
    """Remove our entry from every event; True when the file changed."""
    path = user_hooks_path()
    if not path.exists():
        return False
    try:
        doc = _load(path)
    except Exception:
        return False
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept = [entry for entry in entries if not _is_rewrite_hook(entry)]
        if len(kept) == len(entries):
            continue
        changed = True
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    if not changed:
        return False
    try:
        # A file holding nothing but our entry was ours; leave no stub behind.
        if not hooks and set(doc) <= {"version", "hooks"}:
            path.unlink()
        else:
            _write(path, doc)
    except OSError:
        return False
    return True


def cursor_rewrite_hook_matcher() -> str | None:
    """The installed entry's matcher, ``""`` for none, or None when absent."""
    path = user_hooks_path()
    if not path.exists():
        return None
    try:
        doc = _load(path)
    except Exception:
        return None
    hooks = doc.get("hooks")
    entries = hooks.get(_EVENT) if isinstance(hooks, dict) else None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if _is_rewrite_hook(entry):
            matcher = entry.get("matcher")
            return matcher if isinstance(matcher, str) else ""
    return None

"""Codex transcript adapter.

Codex stores session rollout files in a date-based hierarchy under
``~/.codex/sessions/YYYY/MM/DD/``. This adapter keeps the implementation
simple and best-effort: it locates those files for a repo and turns each
JSONL line into the shared session ``Event`` shape used by the miners.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from repowise.core.fs_walk import iter_glob
from repowise.core.sessions.adapters.base import (
    INTENT_SHELL_CALLS,
    INTENT_TOOL_CALLS,
    INTENT_TURNS,
    HarnessAdapter,
    RawPrefilter,
)
from repowise.core.sessions.adapters.claude_code import parse_timestamp
from repowise.core.sessions.adapters.registry import register_adapter
from repowise.core.sessions.events import Event, ToolResult, ToolUse

_TOOL_CALL_RE = re.compile(r"tools\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_SHELL_COMMAND_RE = re.compile(r'"command"\s*:\s*"([^"]*)"')
_PATH_RE = re.compile(r"(?m)^([^\r\n]+(?:\\|/)[^\r\n]+)$")

@register_adapter
class CodexAdapter(HarnessAdapter):
    """Normalizes Codex session JSONL into the shared Event stream."""

    name: ClassVar[str] = "codex"

    def __init__(self):
        self._tool_calls: dict[str, ToolUse] = {}
        self._current_session: str | None = None

    def discover(self, repo_root: Path, *, projects_root: Path | None = None) -> list[Path]:
        root = projects_root if projects_root is not None else Path.home() / ".codex" / "sessions"
        if not root.is_dir():
            return []
        return sorted(path for path in iter_glob(root, "*.jsonl") if path.is_file())

    def normalize(self, raw_line: str) -> Event | None:
        try:
            entry = json.loads(raw_line)
        except ValueError:
            return None
        if not isinstance(entry, dict):
            return None

        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        entry_kind = (
            entry.get("type") if isinstance(entry.get("type"), str) and entry.get("type") else None
        )
        payload_kind = (
            payload.get("type")
            if isinstance(payload.get("type"), str) and payload.get("type")
            else None
        )
        kind = _event_kind(entry_kind, payload_kind, payload)

        event = Event(
            kind=kind,
            ts=parse_timestamp(_first_non_empty(entry.get("timestamp"), payload.get("timestamp"))),
            session_id=_first_non_empty_str(
                _str_or_none(payload.get("session_id")),
                _str_or_none(entry.get("session_id")),
            ),
            cwd=_first_non_empty_str(
                _str_or_none(payload.get("cwd")), _str_or_none(entry.get("cwd"))
            ),
            usage=None,
            message_id=_str_or_none(payload.get("id")) or _str_or_none(entry.get("message_id")),
            model=_str_or_none(payload.get("model")) or _str_or_none(entry.get("model")),
            sidechain=False,
            is_meta=entry_kind == "session_meta",
            is_compact_summary=False,
        )

        if entry_kind == "session_meta":
            event.text = ""
            self._current_session = event.session_id
            return event

        if event.session_id is None:
            event.session_id = self._current_session

        text = _extract_text(
            entry.get("text"), payload.get("message"), payload.get("content"), payload.get("output")
        )
        if text:
            event.text = text

        if entry_kind == "response_item" and isinstance(payload, dict):
            self._fill_response_item(event, payload)

        for tool_call in entry.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = tool_call.get("name")
            if isinstance(tool_id, str) and isinstance(name, str):
                tool_input = payload.get("input")

                normalized_input = tool_input if isinstance(tool_input, (dict, str)) else {}
                event.tool_uses.append(
                    ToolUse(
                        id=tool_id,
                        name=_normalize_tool_name(name, normalized_input),
                        input=normalized_input,
                    )
                )

        return event

    def prefilter(self, intent: str) -> RawPrefilter | None:
        if intent == INTENT_SHELL_CALLS:
            return lambda raw: (
                '"type":"custom_tool_call"' in raw
                or '"type":"custom_tool_call_output"' in raw
            )

        if intent == INTENT_TOOL_CALLS:
            return lambda raw: (
                '"type":"custom_tool_call"' in raw
                or '"type":"custom_tool_call_output"' in raw
                or '"type":"function_call"' in raw
                or '"type":"function_call_output"' in raw
            )

        if intent == INTENT_TURNS:
            return lambda raw: (
                '"type":"event_msg"' in raw
                or '"type":"response_item"' in raw
            )

        return None

    def begin_file(self, path: Path | None = None) -> None:
        self._tool_calls.clear()
        self._current_session = None

    def end_file(self) -> None:
        self._tool_calls.clear()
        self._current_session = None

    def _fill_response_item(self, event: Event, payload: dict[str, Any]) -> None:
        """Handles response_item entries in Codex transcripts, and converts them to either text, tool uses or tool results

        Codex uses response_item entries to represent things like message, tool call or tool results
        """
        payload_type = payload.get("type")
        if payload_type == "message":
            event.text = _extract_text(None, None, payload.get("content"), None) or event.text
            event.message_id = _str_or_none(payload.get("id")) or event.message_id
            event.model = _str_or_none(payload.get("model")) or event.model
            return

        if payload_type == "custom_tool_call":
            tool_id = payload.get("call_id")
            name = payload.get("name")

            if isinstance(tool_id, str) and isinstance(name, str):
                tool_input = payload.get("input")

                if isinstance(tool_input, dict):
                    normalized_input = tool_input
                elif isinstance(tool_input, str):
                    normalized_input = {"command": tool_input}
                else:
                    normalized_input = {}

                normalized_name = _normalize_tool_name(name, normalized_input)

                tool = ToolUse(
                    id=tool_id,
                    name=normalized_name,
                    input=normalized_input,
                )

                self._tool_calls[tool_id] = tool
                event.tool_uses.append(tool)
            return

        if payload_type == "custom_tool_call_output":
            output = payload.get("output")
            text = _extract_text(None, None, output, None)

            if text:
                event.text = text

            call_id = payload.get("call_id")
            if not isinstance(call_id, str):
                return

            tool = self._tool_calls.pop(call_id, None)

            if tool is not None and tool.name == "search_codebase":
                rewritten = _rewrite_search_output(output)

                results = rewritten["result"]["results"]
                if results:
                    tool.input["path"] = results[0]["file"]

                output = json.dumps(rewritten)

            event.tool_results.append(
                ToolResult(
                    tool_use_id=call_id,
                    is_error=False,
                    content=output,
                    payload=output,
                )
            )

        if payload_type == "function_call":
            tool_id = payload.get("call_id")
            name = payload.get("name")

            if not isinstance(tool_id, str) or not isinstance(name, str):
                return

            arguments = payload.get("arguments")

            if isinstance(arguments, str):
                try:
                    normalized_input = json.loads(arguments)
                except ValueError:
                    normalized_input = {}
            elif isinstance(arguments, dict):
                normalized_input = arguments
            else:
                normalized_input = {}

            normalized_name = _normalize_tool_name(name, normalized_input)

            tool = ToolUse(
                id=tool_id,
                name=normalized_name,
                input=normalized_input,
            )

            self._tool_calls[tool_id] = tool
            event.tool_uses.append(tool)
            return
        
        if payload_type == "function_call_output":
            output = payload.get("output")
            text = _extract_text(None, None, output, None)

            if(text ):
                event.text = text

            call_id = payload.get("call_id")

            if not isinstance(call_id, str):
                return

            self._tool_calls.pop(call_id, None)

            event.tool_results.append(
                ToolResult(
                    tool_use_id=call_id,
                    is_error=False,
                    content=output,
                    payload=output,
                )
            )
            return


def _rewrite_search_output(output: list[dict[str, str]]) -> dict:
    """Convert rg --files output into the MCP search_codebase result shape."""

    texts = []
    for block in output:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)

    full_text = "\n".join(texts)

    results = []

    for line in full_text.splitlines():
        line = line.strip()
        if not line:
            continue

        if (
            line.startswith("Script ")
            or line.startswith("Exit code:")
            or line.startswith("Wall time")
            or line == "Output:"
        ):
            continue

        results.append({"file": line.replace("\\", "/")})

    return {
        "result": {
            "results": results,
        }
    }


def _normalize_tool_name(name: str, tool_input: dict[str, Any]) -> str:
    aliases = {
        "search": "search_codebase",
        "search_codebase": "search_codebase",
        "answer": "get_answer",
        "get_answer": "get_answer",
        "edit": "edit_file",
        "apply_patch": "edit_file",
        "write_file": "edit_file",
        "bash": "bash",
        "run_command": "bash",
        "exec": "bash",
    }
    if name.lower() != "exec":
        return aliases.get(name.lower(), name)

    command = tool_input.get("command")
    if not isinstance(command, str):
        return "bash"

    match = _TOOL_CALL_RE.search(command)
    if not match:
        return "bash"

    embedded_tool = match.group(1)
    if embedded_tool == "shell_command":
        shell_match = _SHELL_COMMAND_RE.search(command)
        if not shell_match:
            return "bash"

        shell_command = shell_match.group(1)

        if re.search(r"\brg(?:\.exe)?\b", shell_command):
            return "search_codebase"

        return "bash"

    return aliases.get(embedded_tool.lower(), embedded_tool)


def _event_kind(entry_kind: str | None, payload_kind: str | None, payload: dict[str, Any]) -> str:
    if entry_kind == "session_meta":
        return "session_meta"
    if entry_kind == "response_item":
        if payload_kind == "message":
            role = payload.get("role")
            if role == "user":
                return "user"
            if role == "assistant":
                return "assistant"
            if role == "developer":
                return "system"
        if payload_kind == "custom_tool_call":
            return "assistant"
        if payload_kind == "custom_tool_call_output":
            return "assistant"
        return payload_kind or "assistant"
    if entry_kind == "event_msg":
        if payload_kind == "user_message":
            return "user"
        if payload_kind == "agent_message":
            return "assistant"
        return payload_kind or "assistant"
    return entry_kind or "assistant"


def _extract_text(*candidates: Any) -> str:
    for candidate in candidates:
        if isinstance(candidate, str):
            if candidate:
                return candidate
            continue
        if isinstance(candidate, list):
            parts: list[str] = []
            for item in candidate:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        parts.append(text)
                    else:
                        kind = item.get("type")
                        if kind in {"input_text", "output_text"}:
                            text = item.get("text")
                            if isinstance(text, str) and text:
                                parts.append(text)
                elif isinstance(item, str) and item:
                    parts.append(item)
            if parts:
                return "\n".join(parts)
        elif isinstance(candidate, dict):
            if isinstance(candidate.get("message"), str):
                return candidate["message"]
    return ""


def _first_non_empty_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None

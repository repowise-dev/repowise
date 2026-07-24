"""Codex transcript adapter.

Codex stores session rollout files in a date-based hierarchy under
``~/.codex/sessions/YYYY/MM/DD/``. This adapter keeps the implementation
simple and best-effort: it locates those files for a repo and turns each
JSONL line into the shared session ``Event`` shape used by the miners.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from repowise.core.sessions.adapters.base import HarnessAdapter
from repowise.core.sessions.events import Event, ToolResult, ToolUse
from repowise.core.sessions.adapters.claude_code import parse_timestamp
from repowise.core.fs_walk import iter_glob


class CodexAdapter(HarnessAdapter):
    """Normalizes Codex session JSONL into the shared Event stream."""

    name: ClassVar[str] = "codex"

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
        entry_kind = entry.get("type") if isinstance(entry.get("type"), str) and entry.get("type") else None
        payload_kind = payload.get("type") if isinstance(payload.get("type"), str) and payload.get("type") else None
        kind = _event_kind(entry_kind, payload_kind, payload)

        event = Event(
            kind=kind,
            ts=parse_timestamp(_first_non_empty(entry.get("timestamp"), payload.get("timestamp"))),
            session_id=_first_non_empty_str(
                _str_or_none(payload.get("session_id")),
                _str_or_none(entry.get("session_id")),
                _str_or_none(payload.get("id")),
            ),
            cwd=_first_non_empty_str(_str_or_none(payload.get("cwd")), _str_or_none(entry.get("cwd"))),
            usage=None,
            message_id=_str_or_none(payload.get("id")) or _str_or_none(entry.get("message_id")),
            model=_str_or_none(payload.get("model")) or _str_or_none(entry.get("model")),
            sidechain=False,
            is_meta=entry_kind == "session_meta",
            is_compact_summary=False,
        )

        if entry_kind == "session_meta":
            event.text = ""
            return event

        text = _extract_text(entry.get("text"), payload.get("message"), payload.get("content"), payload.get("output"))
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
                event.tool_uses.append(
                    ToolUse(
                        id=tool_id,
                        name=_normalize_tool_name(name),
                        input=tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {},
                    )
                )

        return event

    @staticmethod
    def _fill_response_item(event: Event, payload: dict[str, Any]) -> None:
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
            tool_id = payload.get("call_id") or payload.get("id")
            name = payload.get("name")
            if isinstance(tool_id, str) and isinstance(name, str):
                event.tool_uses.append(
                    ToolUse(
                        id=tool_id,
                        name=_normalize_tool_name(name),
                        input=payload.get("input") if isinstance(payload.get("input"), dict) else {},
                    )
                )
            return

        if payload_type == "custom_tool_call_output":
            output = payload.get("output")
            text = _extract_text(None, None, output, None)
            if text:
                event.text = text
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                event.tool_results.append(
                    ToolResult(tool_use_id=call_id, is_error=False, content=output, payload=output)
                )


def _normalize_tool_name(name: str) -> str:
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
    return aliases.get(name.lower(), name)


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
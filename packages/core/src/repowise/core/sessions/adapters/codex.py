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
#: ``rg`` prints ``path:line:text`` for a content match and a bare path for
#: ``--files``. Only the leading path is a file; the rest is the matched line.
_RG_MATCH_RE = re.compile(r"^(?P<path>[^:]+(?::[^:\\/]*[\\/][^:]*)?):\d+:")
#: Lines the exec wrapper prints around a command's real output.
_EXEC_NOISE = ("Script ", "Exit code:", "Wall time", "Output:")


def _tool_calls_prefilter(raw: str) -> bool:
    return (
        '"type":"custom_tool_call"' in raw
        or '"type":"custom_tool_call_output"' in raw
        or '"type":"function_call"' in raw
        or '"type":"function_call_output"' in raw
    )


def _turns_prefilter(raw: str) -> bool:
    return '"type":"event_msg"' in raw or '"type":"response_item"' in raw


@register_adapter
class CodexAdapter(HarnessAdapter):
    """Normalizes Codex session JSONL into the shared Event stream."""

    name: ClassVar[str] = "codex"

    def __init__(self):
        self._tool_calls: dict[str, ToolUse] = {}
        self._current_session: str | None = None
        self._current_cwd: str | None = None

    def discover(self, repo_root: Path, *, projects_root: Path | None = None) -> list[Path]:
        """Every Codex rollout, not just this repo's.

        Codex files its sessions by date rather than by project, so the
        transcript path carries no repo. The repo a line belongs to is on the
        ``cwd`` threaded out of ``session_meta``, which is what the miners
        scope on, so returning the full set here is correct rather than lazy.
        """
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
            self._current_cwd = event.cwd
            return event

        if event.session_id is None:
            event.session_id = self._current_session
        # Codex writes cwd once, in session_meta. The miners scope every event
        # on its own cwd, so a line without one counts toward whichever repo is
        # being mined; threading it is what keeps one project's sessions out of
        # another's decisions.
        if event.cwd is None:
            event.cwd = self._current_cwd

        text = _extract_text(
            entry.get("text"), payload.get("message"), payload.get("content"), payload.get("output")
        )
        if text:
            event.text = text

        if entry_kind == "response_item" and isinstance(payload, dict):
            self._fill_response_item(event, payload)

        return event

    def prefilter(self, intent: str) -> RawPrefilter | None:
        # Both call shapes carry shell work, so both gates admit both. Codex
        # wraps a shell command in an `exec` custom_tool_call *and* calls
        # `shell_command` as a plain function_call; on real rollouts the second
        # is the larger half, so a shell gate that reads only the first drops
        # most of what it exists to find. Substring tests on the raw string,
        # never a parse: see HarnessAdapter.prefilter.
        if intent in (INTENT_SHELL_CALLS, INTENT_TOOL_CALLS):
            return _tool_calls_prefilter

        if intent == INTENT_TURNS:
            return _turns_prefilter

        return None

    def begin_file(self, path: Path | None = None) -> None:
        self._tool_calls.clear()
        self._current_session = None
        self._current_cwd = None

    def end_file(self) -> None:
        self._tool_calls.clear()
        self._current_session = None
        self._current_cwd = None

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
                if rewritten is not None:
                    _bind_first_file(tool, rewritten["result"]["results"])
                    output = json.dumps(rewritten)

            event.tool_results.append(
                ToolResult(
                    tool_use_id=call_id,
                    is_error=False,
                    content=output,
                    payload=output,
                )
            )
            return

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

            if text:
                event.text = text

            call_id = payload.get("call_id")

            if not isinstance(call_id, str):
                return

            tool = self._tool_calls.pop(call_id, None)

            # An MCP search arrives as {"query": ...} with no path key, so
            # event_files() would bind the record to nothing. The result names
            # the file; lift the first one onto the call that asked for it.
            if tool is not None and tool.name == "search_codebase" and isinstance(output, str):
                _bind_first_file(tool, _mcp_search_results(output))

            event.tool_results.append(
                ToolResult(
                    tool_use_id=call_id,
                    is_error=False,
                    content=output,
                    payload=output,
                )
            )
            return


def _bind_first_file(tool: ToolUse, results: list[dict[str, str]]) -> None:
    """Point *tool* at the first file its search returned.

    ``event_files`` reads ``FILE_INPUT_KEYS`` off a tool's input, and a search
    call names a query rather than a file, so without this a search that found
    something binds a decision record to nothing. Only fills a key that is not
    already there: an explicit argument outranks an inferred one.
    """
    if not isinstance(tool.input, dict) or tool.input.get("path"):
        return
    for result in results:
        file_path = result.get("file") if isinstance(result, dict) else None
        if isinstance(file_path, str) and file_path:
            tool.input["path"] = file_path
            return


def _mcp_search_results(output: str) -> list[dict[str, str]]:
    """The ``results`` list out of an MCP tool's output, or empty.

    The MCP transport wraps its JSON in timing prose (``Wall time: ...``,
    ``Output:``, then the object), so parse from the first brace rather than
    from the first character.
    """
    start = output.find("{")
    if start == -1:
        return []
    try:
        parsed = json.loads(output[start:])
    except ValueError:
        return []
    if not isinstance(parsed, dict):
        return []
    result = parsed.get("result")
    results = result.get("results") if isinstance(result, dict) else None
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _rewrite_search_output(output: Any) -> dict | None:
    """``rg`` output as the MCP ``search_codebase`` result shape, or None.

    None rather than an empty envelope when the output is not the block list
    this understands: overwriting a real tool result with ``{"results": []}``
    loses the output, and ``normalize`` may not raise on content it cannot
    read, which a bare iteration over a non-list would.

    Only the leading path of an ``rg`` line becomes a file. ``rg`` prints
    ``path:line:text`` for a content match, and taking the whole line would
    hand the decision miner a "file" that does not exist.
    """
    if not isinstance(output, list):
        return None

    texts = [
        block["text"]
        for block in output
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]

    results: list[dict[str, str]] = []
    for raw_line in "\n".join(texts).splitlines():
        line = raw_line.strip()
        if not line or line.startswith(_EXEC_NOISE):
            continue
        match = _RG_MATCH_RE.match(line)
        candidate = match.group("path") if match else line
        if "/" in candidate or "\\" in candidate:
            results.append({"file": candidate.replace("\\", "/")})

    return {"result": {"results": results}}


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

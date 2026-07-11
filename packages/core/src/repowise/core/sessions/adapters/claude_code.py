"""Claude Code transcript adapter.

Claude Code writes one JSONL file per session under
``~/.claude/projects/<munged-cwd>/``, where the munged name replaces every
non ``[A-Za-z0-9-]`` character of the session cwd with ``-``. Each line is
one entry; the shapes this adapter understands (verified against real
transcripts on this machine, 2026-07):

- ``assistant`` entries: top-level ``cwd``/``timestamp``/``sessionId``/
  ``isSidechain``; ``message`` carries ``id``, ``model``, ``usage`` and a
  ``content[]`` list with ``text`` and ``tool_use`` blocks.
- ``user`` entries: plain prompts (string or text-block content, sometimes
  ``isMeta`` or ``isCompactSummary``) or tool results (``content[]`` with
  ``tool_result`` blocks plus a top-level ``toolUseResult`` whose
  string-vs-dict shape distinguishes command failures from successes).
- Other types (``system``, ``file-history-snapshot``, ``queue-operation``,
  ...) normalize with their type passed through so consumers can ignore
  them by kind.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from repowise.core.sessions.adapters.base import HarnessAdapter
from repowise.core.sessions.events import Event, ToolResult, ToolUse

_NON_SLUG_RE = re.compile(r"[^A-Za-z0-9-]")


def transcript_dir_for(repo_root: Path, projects_root: Path | None = None) -> Path:
    """The Claude Code transcript directory for sessions started at *repo_root*.

    ``C:\\Users\\x\\repo`` munges to ``C--Users-x-repo``.
    """
    root = projects_root if projects_root is not None else Path.home() / ".claude" / "projects"
    return root / _NON_SLUG_RE.sub("-", str(repo_root))


def parse_timestamp(value: Any) -> float | None:
    """Epoch seconds from an ISO-8601 transcript timestamp, or None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class ClaudeCodeAdapter(HarnessAdapter):
    """Normalizes Claude Code session JSONL into the shared Event stream."""

    name: ClassVar[str] = "claude_code"

    def discover(self, repo_root: Path, *, projects_root: Path | None = None) -> list[Path]:
        directory = transcript_dir_for(repo_root, projects_root)
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.jsonl"))

    def normalize(self, raw_line: str) -> Event | None:
        try:
            entry = json.loads(raw_line)
        except ValueError:
            return None
        if not isinstance(entry, dict):
            return None

        kind = entry.get("type")
        if not isinstance(kind, str) or not kind:
            return None
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        usage = message.get("usage")

        event = Event(
            kind=kind,
            ts=parse_timestamp(entry.get("timestamp")),
            session_id=_str_or_none(entry.get("sessionId")),
            cwd=_str_or_none(entry.get("cwd")),
            usage=usage if isinstance(usage, dict) else None,
            message_id=_str_or_none(message.get("id")),
            model=_str_or_none(message.get("model")),
            sidechain=bool(entry.get("isSidechain")),
            is_meta=bool(entry.get("isMeta")),
            is_compact_summary=bool(entry.get("isCompactSummary")),
        )

        content = message.get("content")
        if isinstance(content, str):
            event.text = content
        elif isinstance(content, list):
            self._fill_blocks(event, content, entry.get("toolUseResult"))
        return event

    @staticmethod
    def _fill_blocks(event: Event, content: list[Any], tool_use_result: Any) -> None:
        """Populate text and tool blocks from a message ``content[]`` list.

        The top-level ``toolUseResult`` record belongs to the entry, not a
        block; real transcripts carry exactly one ``tool_result`` block per
        entry, so it is attached to every result block seen.
        """
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
            elif block_type == "tool_use":
                block_id = block.get("id")
                name = block.get("name")
                if isinstance(block_id, str) and isinstance(name, str):
                    tool_input = block.get("input")
                    event.tool_uses.append(
                        ToolUse(
                            id=block_id,
                            name=name,
                            input=tool_input if isinstance(tool_input, dict) else {},
                        )
                    )
            elif block_type == "tool_result":
                use_id = block.get("tool_use_id")
                if isinstance(use_id, str):
                    event.tool_results.append(
                        ToolResult(
                            tool_use_id=use_id,
                            is_error=bool(block.get("is_error")),
                            content=block.get("content"),
                            payload=tool_use_result,
                        )
                    )
        event.text = "\n".join(texts)


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None

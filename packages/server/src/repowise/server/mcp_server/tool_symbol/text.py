"""Live file text: reading, slicing and Read-style line numbering."""

from __future__ import annotations

import time
from pathlib import Path

from repowise.server.mcp_server._helpers import (
    read_repo_file_text,
)

_log = __import__("logging").getLogger("repowise.mcp.symbol")


# Safety cap so a misconfigured WikiSymbol row pointing at a giant file can
# never blow up the agent's context window. Nearly every symbol fits, so one
# call serves the whole body; the rare overflow gets a clean `continuation`.
_MAX_SOURCE_LINES = 600


def _read_file_text(repo_path: Path, file_path: str) -> str | None:
    """Read a repo file's live text, or None when unreadable/outside the root."""
    text = read_repo_file_text(repo_path, file_path)
    if text is None:
        _log.warning("get_symbol could not serve %s (unreadable or outside repo root)", file_path)
    return text


def _slice_text(
    text: str,
    start_line: int,
    end_line: int,
    context_lines: int,
    max_lines: int = _MAX_SOURCE_LINES,
) -> tuple[str, int, int, int]:
    """Slice ``text`` to (source, actual_start, actual_end, total_lines).

    Lines are 1-indexed inclusive to match WikiSymbol storage; the range is
    expanded by ``context_lines`` on both sides and capped at ``max_lines``
    (truncating from the tail — the head carries the signature, which is what
    the agent needs to ground itself). The cap is applied AFTER context
    expansion so the returned span never exceeds it.
    """
    lines = text.splitlines()
    total = len(lines)

    s = max(1, min(start_line, total))
    e = max(s, min(end_line, total))
    if context_lines > 0:
        s = max(1, s - context_lines)
        e = min(total, e + context_lines)

    if (e - s + 1) > max_lines:
        e = s + max_lines - 1

    return "\n".join(lines[s - 1 : e]), s, e, total


def _number_lines(source: str, start_line: int) -> str:
    """Prefix each line with its 1-based file line number, Read-style.

    Byte-parity with the agent's own Read tool output (``cat -n`` format:
    right-aligned number, tab, line). Source served in the exact shape of an
    already-performed Read is treated as one — the agent edits from it
    instead of re-Reading the file to "see the real format".
    """
    return "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(source.splitlines(), start_line))


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000

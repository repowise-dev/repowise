"""Repo-local log of MCP tool call latency: ``<repo>/.repowise/mcp_calls.jsonl``.

One JSON object per line, ``{"tool", "ms", "cold"}``. The log never leaves the
machine; ``repowise status`` reads it back as p50 and p90 per tool.
"""

from __future__ import annotations

import json
import math
import threading
from collections import defaultdict
from pathlib import Path

LOG_FILENAME = "mcp_calls.jsonl"
MAX_LINES = 5000
KEEP_LINES = 2500

_lock = threading.Lock()
_seen_tools: set[str] = set()
_line_counts: dict[str, int] = {}


def _log_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".repowise" / LOG_FILENAME


def record_call(repo_root: str | Path | None, tool: str, ms: int) -> bool:
    """Append one call to the log; ``False`` when skipped or the write failed.

    Skipped when the repo has no ``.repowise/`` directory. ``cold`` is true for
    the first call of *tool* in this process. Never raises.
    """
    if not repo_root:
        return False
    path = _log_path(repo_root)
    if not path.parent.is_dir():
        return False
    try:
        with _lock:
            cold = tool not in _seen_tools
            _seen_tools.add(tool)
            row = json.dumps({"tool": tool, "ms": ms, "cold": cold}, separators=(",", ":"))
            key = str(path)
            if key not in _line_counts:
                _line_counts[key] = _count_lines(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(row + "\n")
            _line_counts[key] += 1
            if _line_counts[key] > MAX_LINES:
                lines = path.read_text(encoding="utf-8").splitlines()[-KEEP_LINES:]
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                _line_counts[key] = len(lines)
        return True
    except Exception:
        return False


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _percentile(sorted_values: list[int], pct: int) -> int:
    """Nearest-rank percentile of an ascending, non-empty list."""
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def latency_summary(repo_root: str | Path) -> dict[str, dict[str, int]]:
    """``{tool: {"calls", "p50", "p90"}}`` from the log; empty when it is absent.

    Malformed lines are skipped.
    """
    try:
        text = _log_path(repo_root).read_text(encoding="utf-8")
    except OSError:
        return {}
    by_tool: dict[str, list[int]] = defaultdict(list)
    for line in text.splitlines():
        try:
            row = json.loads(line)
            by_tool[str(row["tool"])].append(int(row["ms"]))
        except (ValueError, KeyError, TypeError):
            continue
    return {
        tool: {
            "calls": len(values),
            "p50": _percentile(sorted(values), 50),
            "p90": _percentile(sorted(values), 90),
        }
        for tool, values in sorted(by_tool.items())
    }

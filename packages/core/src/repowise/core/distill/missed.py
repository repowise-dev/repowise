"""Missed-savings discovery — what did raw agent commands waste?

Scans Claude Code transcript JSONL (``~/.claude/projects/<munged-cwd>/*.jsonl``)
for Bash/PowerShell tool calls inside this repo that were **not** routed
through ``repowise distill``, classifies each command with the same router the
engine uses, and estimates the tokens a filter would have saved using that
filter's conservative measured floor (the per-fixture savings floors asserted
in CI, not the medians).

Read-only and best-effort by contract: malformed lines, unreadable files, or
an absent transcript directory produce an empty report, never an error — this
runs inside ``repowise saved`` and a dashboard endpoint, neither of which may
break because a transcript changed shape.

Privacy: everything stays local. Commands and outputs are read from the
user's own transcript directory on this machine; nothing leaves it.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.router import normalize_command, select_filter

#: Default scan window, in days.
DEFAULT_WINDOW_DAYS = 7.0

#: Conservative per-filter savings ratios — the *floor* each filter's fixture
#: suite asserts in CI (see tests/unit/distill/test_filters.py and
#: test_search_results.py), not the measured medians. Estimates built on
#: these undersell rather than oversell.
RATIO_FLOOR: dict[str, float] = {
    "test_output": 0.40,
    "build_output": 0.40,
    "lint_output": 0.30,
    "git_status": 0.15,
    "git_log": 0.80,
    "git_diff": 0.50,
    "git_diff_stat": 0.50,
    "search_results": 0.40,
    "file_listing": 0.60,
    "logs": 0.50,
}

#: Mirrors the engine's net-positive floor (``MIN_SAVED_TOKENS``): events whose
#: estimated savings fall under it would not have produced a marker anyway.
_MIN_EST_TOKENS = 40

_SHELL_TOOLS = ("Bash", "PowerShell")

_NON_SLUG_RE = re.compile(r"[^A-Za-z0-9-]")


def transcript_dir_for(repo_root: Path, projects_root: Path | None = None) -> Path:
    """The Claude Code transcript directory for sessions started at *repo_root*.

    Claude Code munges the session cwd into a directory name by replacing
    every non ``[A-Za-z0-9-]`` character with ``-`` (``C:\\Users\\x\\repo`` →
    ``C--Users-x-repo``).
    """
    root = projects_root if projects_root is not None else Path.home() / ".claude" / "projects"
    return root / _NON_SLUG_RE.sub("-", str(repo_root))


def empty_report(days: float = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    return {
        "events": 0,
        "raw_tokens": 0,
        "est_saved_tokens": 0,
        "per_filter": {},
        "window_days": days,
    }


def scan_missed_savings(
    repo_root: Path,
    *,
    days: float = DEFAULT_WINDOW_DAYS,
    now: float | None = None,
    projects_root: Path | None = None,
) -> dict[str, Any]:
    """Aggregate foregone savings from raw shell commands in recent transcripts.

    Counts Bash/PowerShell tool calls whose cwd lies inside *repo_root*, that
    were not already ``repowise``-prefixed, that classify to a known filter,
    and whose output was big enough to clear the filter's ``min_lines`` and
    the net-positive floor. Returns totals plus a per-filter breakdown; any
    failure degrades to an empty report.
    """
    try:
        return _scan(Path(repo_root).resolve(), days, now, projects_root)
    except Exception:
        return empty_report(days)


def _scan(
    repo_root: Path, days: float, now: float | None, projects_root: Path | None
) -> dict[str, Any]:
    transcripts = transcript_dir_for(repo_root, projects_root)
    report = empty_report(days)
    if not transcripts.is_dir():
        return report

    cutoff = (now if now is not None else time.time()) - days * 86400.0
    repo_prefix = str(repo_root).lower().rstrip("\\/")
    per_filter: dict[str, dict[str, int]] = {}

    for path in sorted(transcripts.glob("*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff:
                continue  # untouched since the window opened
            _scan_file(path, cutoff, repo_prefix, per_filter)
        except OSError:
            continue

    for stats in per_filter.values():
        report["events"] += stats["events"]
        report["raw_tokens"] += stats["raw_tokens"]
        report["est_saved_tokens"] += stats["est_saved_tokens"]
    report["per_filter"] = dict(
        sorted(per_filter.items(), key=lambda kv: -kv[1]["est_saved_tokens"])
    )
    return report


def _scan_file(
    path: Path, cutoff: float, repo_prefix: str, per_filter: dict[str, dict[str, int]]
) -> None:
    #: tool_use id -> command, for shell calls that passed every command gate.
    pending: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            # Cheap substring gates before paying for json.loads on huge lines.
            if '"tool_use"' in raw and '"command"' in raw:
                _collect_tool_use(raw, cutoff, repo_prefix, pending)
            elif pending and '"toolUseResult"' in raw:
                _collect_result(raw, pending, per_filter)


def _collect_tool_use(raw: str, cutoff: float, repo_prefix: str, pending: dict[str, str]) -> None:
    try:
        entry = json.loads(raw)
    except ValueError:
        return
    if entry.get("type") != "assistant":
        return
    ts = _parse_ts(entry.get("timestamp"))
    if ts is not None and ts < cutoff:
        return
    cwd = str(entry.get("cwd") or "").lower().rstrip("\\/")
    if not cwd.startswith(repo_prefix):
        return
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") not in _SHELL_TOOLS:
            continue
        command = str((block.get("input") or {}).get("command") or "")
        if not command or normalize_command(command).startswith("repowise"):
            continue  # already distilled (or another repowise invocation)
        block_id = block.get("id")
        if isinstance(block_id, str):
            pending[block_id] = command


def _collect_result(
    raw: str, pending: dict[str, str], per_filter: dict[str, dict[str, int]]
) -> None:
    try:
        entry = json.loads(raw)
    except ValueError:
        return
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    block_id = next(
        (
            b.get("tool_use_id")
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ),
        None,
    )
    command = pending.pop(block_id, None) if isinstance(block_id, str) else None
    if command is None:
        return

    output = _result_text(entry.get("toolUseResult"))
    if not output:
        return
    chosen = select_filter(command, output)
    if chosen is None:
        return
    raw_tokens = estimate_tokens(output)
    est = int(raw_tokens * RATIO_FLOOR.get(chosen.name, 0.0))
    if len(output.splitlines()) < chosen.min_lines or est < _MIN_EST_TOKENS:
        return

    stats = per_filter.setdefault(
        chosen.name, {"events": 0, "raw_tokens": 0, "est_saved_tokens": 0}
    )
    stats["events"] += 1
    stats["raw_tokens"] += raw_tokens
    stats["est_saved_tokens"] += est


def _result_text(result: Any) -> str:
    """stdout+stderr from a shell ``toolUseResult``, tolerating shape drift."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        parts = [str(result.get(key) or "") for key in ("stdout", "stderr")]
        return "\n".join(p for p in parts if p)
    return ""


def _parse_ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None

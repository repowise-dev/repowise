"""Missed-savings discovery — what did raw agent commands waste?

Scans Claude Code transcripts (via the shared
:mod:`repowise.core.sessions` layer) for Bash/PowerShell tool calls inside
this repo that were **not** routed through ``repowise distill``, classifies
each command with the same router the engine uses, and estimates the tokens
a filter would have saved using that filter's conservative measured floor
(the per-fixture savings floors asserted in CI, not the medians).

Read-only and best-effort by contract: malformed lines, unreadable files, or
an absent transcript directory produce an empty report, never an error — this
runs inside ``repowise saved`` and a dashboard endpoint, neither of which may
break because a transcript changed shape.

Privacy: everything stays local. Commands and outputs are read from the
user's own transcript directory on this machine; nothing leaves it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.router import normalize_command, select_filter
from repowise.core.sessions import ClaudeCodeAdapter, Event, transcript_dir_for

__all__ = ["scan_missed_savings", "transcript_dir_for"]

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
    # Data-driven filters (filters_toml/*.toml). Conservative floors well under
    # their measured medians (install ~99%, infra plan ~80%) so estimates
    # undersell; the per-file CI floor lives in each .toml's [meta].
    "install_output": 0.50,
    "infra_plan": 0.50,
}

#: Mirrors the engine's net-positive floor (``MIN_SAVED_TOKENS``): events whose
#: estimated savings fall under it would not have produced a marker anyway.
_MIN_EST_TOKENS = 40

_SHELL_TOOLS = ("Bash", "PowerShell")

_ADAPTER = ClaudeCodeAdapter()


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


def _prefilter(raw: str) -> bool:
    """Only shell tool_use lines and result lines are worth parsing."""
    return ('"tool_use"' in raw and '"command"' in raw) or '"toolUseResult"' in raw


def _scan_file(
    path: Path, cutoff: float, repo_prefix: str, per_filter: dict[str, dict[str, int]]
) -> None:
    #: tool_use id -> command, for shell calls that passed every command gate.
    pending: dict[str, str] = {}
    for event in _ADAPTER.iter_events(path, prefilter=_prefilter):
        if event.kind == "assistant" and event.tool_uses:
            _collect_tool_use(event, cutoff, repo_prefix, pending)
        elif pending and event.tool_results:
            _collect_result(event, pending, per_filter)


def _collect_tool_use(
    event: Event, cutoff: float, repo_prefix: str, pending: dict[str, str]
) -> None:
    if event.ts is not None and event.ts < cutoff:
        return
    cwd = (event.cwd or "").lower().rstrip("\\/")
    if not cwd.startswith(repo_prefix):
        return
    for use in event.tool_uses:
        if use.name not in _SHELL_TOOLS:
            continue
        command = str(use.input.get("command") or "")
        if not command or normalize_command(command).startswith("repowise"):
            continue  # already distilled (or another repowise invocation)
        pending[use.id] = command


def _collect_result(
    event: Event, pending: dict[str, str], per_filter: dict[str, dict[str, int]]
) -> None:
    result = event.tool_results[0]
    command = pending.pop(result.tool_use_id, None)
    if command is None:
        return

    output = _result_text(result.payload)
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

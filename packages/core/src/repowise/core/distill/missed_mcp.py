"""Missed MCP savings — what did repeated file reads waste?

The sibling :mod:`repowise.core.distill.missed` accounts for *shell commands*
a distill filter would have compressed. This module covers the other big
exploration sink: an agent reading the **same file** more than once in a
session. The first read puts the file in context; a later *full* re-read of
the unchanged file re-bills the whole thing, when a targeted ``get_symbol``
or a line-range read would have returned a fraction.

It scans the same Claude Code transcript JSONL
(``~/.claude/projects/<munged-cwd>/*.jsonl``) as the missed-savings scan,
tracks per-file read and edit order within each session, and credits a
conservative fraction of each wasteful re-read's tokens as foregone savings.

Read-only and best-effort by the same contract as
:mod:`repowise.core.distill.missed`: malformed lines, unreadable files, or an
absent transcript directory produce an empty report, never an error.

Privacy: everything stays local. Reads are taken from the user's own
transcript directory on this machine; nothing leaves it.

What counts as a wasteful re-read (high precision on purpose):

  - the file was already read earlier this session;
  - no Edit/Write/MultiEdit to it landed after that prior read (an edit makes
    a re-read justified — the content changed);
  - the current read is a *full* read (no ``offset``/``limit``). A targeted
    range re-read is the behavior we recommend, so it is never counted.
"""

from __future__ import annotations

import json
import os.path
import time
from pathlib import Path
from typing import Any

from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.missed import _parse_ts, transcript_dir_for

#: Default scan window, in days. Matches the missed-savings scan.
DEFAULT_WINDOW_DAYS = 7.0

#: Conservative fraction of a re-read's tokens credited as saveable. A full
#: re-read of an unchanged file is largely redundant (the content is already
#: in context), but a ``get_symbol`` replacement still returns the one symbol
#: the agent wanted, so we undersell rather than claim the whole read back.
REREAD_FLOOR = 0.5

#: Mirrors the engine's net-positive floor: a re-read this small would not be
#: worth flagging even if avoided.
_MIN_EST_TOKENS = 40

_MUTATING_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def empty_report(days: float = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    return {
        "events": 0,
        "raw_tokens": 0,
        "est_saved_tokens": 0,
        "per_file": {},
        "window_days": days,
    }


def scan_missed_mcp_savings(
    repo_root: Path,
    *,
    days: float = DEFAULT_WINDOW_DAYS,
    now: float | None = None,
    projects_root: Path | None = None,
) -> dict[str, Any]:
    """Aggregate foregone savings from wasteful file re-reads in transcripts.

    Returns totals plus a per-file breakdown; any failure degrades to an
    empty report.
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
    per_file: dict[str, dict[str, int]] = {}

    for path in sorted(transcripts.glob("*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff:
                continue  # untouched since the window opened
            _scan_file(path, cutoff, repo_prefix, repo_root, per_file)
        except OSError:
            continue

    for stats in per_file.values():
        report["events"] += stats["events"]
        report["raw_tokens"] += stats["raw_tokens"]
        report["est_saved_tokens"] += stats["est_saved_tokens"]
    report["per_file"] = dict(sorted(per_file.items(), key=lambda kv: -kv[1]["est_saved_tokens"]))
    return report


def _rel(file_path: str, repo_root: Path) -> str:
    """Repo-relative POSIX display path; falls back to the raw path off-root."""
    try:
        rel = os.path.relpath(file_path, str(repo_root))
    except (ValueError, OSError):
        return file_path
    if rel.startswith(".."):
        return file_path
    return rel.replace("\\", "/")


def _scan_file(
    path: Path,
    cutoff: float,
    repo_prefix: str,
    repo_root: Path,
    per_file: dict[str, dict[str, int]],
) -> None:
    """Replay one session transcript, crediting wasteful re-reads.

    State is per file (transcript): each ``*.jsonl`` is one session, so read
    and edit history never leaks across sessions.
    """
    seq = 0
    #: Read tool_use id -> (file_path, is_partial, seq_at_use), awaiting result.
    pending: dict[str, tuple[str, bool, int]] = {}
    last_read_seq: dict[str, int] = {}
    last_edit_seq: dict[str, int] = {}

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if '"tool_use"' in raw:
                seq = _collect_tool_use(raw, cutoff, repo_prefix, seq, pending, last_edit_seq)
            elif pending and '"tool_result"' in raw:
                _collect_result(raw, pending, last_read_seq, last_edit_seq, repo_root, per_file)


def _collect_tool_use(
    raw: str,
    cutoff: float,
    repo_prefix: str,
    seq: int,
    pending: dict[str, tuple[str, bool, int]],
    last_edit_seq: dict[str, int],
) -> int:
    """Record Read tool_use ids (awaiting result) and Edit ordering. Returns seq."""
    try:
        entry = json.loads(raw)
    except ValueError:
        return seq
    if entry.get("type") != "assistant":
        return seq
    ts = _parse_ts(entry.get("timestamp"))
    if ts is not None and ts < cutoff:
        return seq
    cwd = str(entry.get("cwd") or "").lower().rstrip("\\/")
    if not cwd.startswith(repo_prefix):
        return seq
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return seq

    seq += 1
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if not isinstance(file_path, str) or not file_path.strip():
            continue
        if name == "Read":
            partial = tool_input.get("offset") is not None or tool_input.get("limit") is not None
            block_id = block.get("id")
            if isinstance(block_id, str):
                pending[block_id] = (file_path, partial, seq)
        elif name in _MUTATING_TOOLS:
            last_edit_seq[file_path] = seq
    return seq


def _collect_result(
    raw: str,
    pending: dict[str, tuple[str, bool, int]],
    last_read_seq: dict[str, int],
    last_edit_seq: dict[str, int],
    repo_root: Path,
    per_file: dict[str, dict[str, int]],
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
    record = pending.pop(block_id, None) if isinstance(block_id, str) else None
    if record is None:
        return
    file_path, partial, read_seq = record

    prior_read = last_read_seq.get(file_path)
    # A wasteful re-read: read before, no edit landed at or after that prior
    # read, and this is a full read. An edit sharing the prior read's turn
    # (same seq) counts as "edited since" and justifies the re-read, so the
    # comparison is strict. Record this read's order regardless so the next
    # re-read compares against the latest read.
    is_waste = (
        prior_read is not None and not partial and last_edit_seq.get(file_path, -1) < prior_read
    )
    last_read_seq[file_path] = read_seq
    if not is_waste:
        return

    output = _read_output_text(entry.get("toolUseResult"))
    if not output:
        return
    raw_tokens = estimate_tokens(output)
    est = int(raw_tokens * REREAD_FLOOR)
    if est < _MIN_EST_TOKENS:
        return

    key = _rel(file_path, repo_root)
    stats = per_file.setdefault(key, {"events": 0, "raw_tokens": 0, "est_saved_tokens": 0})
    stats["events"] += 1
    stats["raw_tokens"] += raw_tokens
    stats["est_saved_tokens"] += est


def _read_output_text(result: Any) -> str:
    """File content from a Read ``toolUseResult``, tolerating shape drift.

    Claude Code serializes a Read result as ``{"type": "text", "file":
    {"content": "...", ...}}``; older or partial shapes may carry a plain
    string. Anything else yields an empty string (skipped).
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        file_block = result.get("file")
        if isinstance(file_block, dict):
            content = file_block.get("content")
            if isinstance(content, str):
                return content
        content = result.get("content")
        if isinstance(content, str):
            return content
    return ""

"""Missed MCP savings — wasteful file re-read detection over transcripts.

Transcript lines follow the Claude Code JSONL shape: an assistant entry with
``message.content[]`` ``tool_use`` blocks plus top-level ``cwd``/``timestamp``,
and a paired user entry carrying ``message.content[].tool_result`` plus a
top-level ``toolUseResult`` (for Read: ``{"type": "text", "file": {...}}``).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.saved_cmd import saved_command
from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.missed import transcript_dir_for
from repowise.core.distill.missed_mcp import REREAD_FLOOR, scan_missed_mcp_savings

NOW = time.time()

#: Big enough that half its tokens clear the net-positive floor (40).
BIG_CONTENT = "x = 1  # padding padding padding\n" * 120


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _read_pair(
    file_path: str,
    content: str,
    *,
    cwd: str,
    block_id: str,
    ts: float = NOW,
    offset: int | None = None,
    limit: int | None = None,
) -> list[dict]:
    tool_input: dict = {"file_path": file_path}
    if offset is not None:
        tool_input["offset"] = offset
    if limit is not None:
        tool_input["limit"] = limit
    return [
        {
            "type": "assistant",
            "cwd": cwd,
            "timestamp": _iso(ts),
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": block_id, "name": "Read", "input": tool_input}
                ],
            },
        },
        {
            "type": "user",
            "cwd": cwd,
            "timestamp": _iso(ts + 1),
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": block_id, "content": "..."}],
            },
            "toolUseResult": {
                "type": "text",
                "file": {
                    "filePath": file_path,
                    "content": content,
                    "numLines": content.count("\n"),
                },
            },
        },
    ]


def _edit_entry(file_path: str, *, cwd: str, ts: float = NOW, tool: str = "Edit") -> dict:
    return {
        "type": "assistant",
        "cwd": cwd,
        "timestamp": _iso(ts),
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "ed1", "name": tool, "input": {"file_path": file_path}}
            ],
        },
    }


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "myrepo"
    (root / ".repowise").mkdir(parents=True)
    return root


@pytest.fixture()
def projects(tmp_path: Path, repo: Path) -> Path:
    root = tmp_path / "projects"
    transcript_dir_for(repo, root).mkdir(parents=True)
    return root


def _write_session(projects: Path, repo: Path, entries: list[dict], name: str = "s1") -> Path:
    path = transcript_dir_for(repo, projects) / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def test_full_reread_of_unchanged_file_counted(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    entries = _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1") + _read_pair(
        f, BIG_CONTENT, cwd=str(repo), block_id="r2"
    )
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 1
    raw = estimate_tokens(BIG_CONTENT)
    assert report["per_file"]["a.py"]["raw_tokens"] == raw
    assert report["est_saved_tokens"] == int(raw * REREAD_FLOOR)


def test_single_read_not_counted(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    _write_session(projects, repo, _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1"))
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_partial_reread_not_counted(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    entries = _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1") + _read_pair(
        f, BIG_CONTENT, cwd=str(repo), block_id="r2", offset=10, limit=20
    )
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_edit_between_reads_is_not_waste(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    entries = [
        *_read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1"),
        _edit_entry(f, cwd=str(repo)),
        *_read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r2"),
    ]
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_three_full_reads_count_two(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    entries = (
        _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1")
        + _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r2")
        + _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r3")
    )
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 2


def test_tiny_reread_below_floor_skipped(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    entries = _read_pair(f, "ok\n", cwd=str(repo), block_id="r1") + _read_pair(
        f, "ok\n", cwd=str(repo), block_id="r2"
    )
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_other_cwd_skipped(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    entries = _read_pair(f, BIG_CONTENT, cwd=r"C:\elsewhere", block_id="r1") + _read_pair(
        f, BIG_CONTENT, cwd=r"C:\elsewhere", block_id="r2"
    )
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_distinct_files_isolated(repo: Path, projects: Path) -> None:
    a, b = str(repo / "a.py"), str(repo / "b.py")
    entries = _read_pair(a, BIG_CONTENT, cwd=str(repo), block_id="r1") + _read_pair(
        b, BIG_CONTENT, cwd=str(repo), block_id="r2"
    )
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_old_entries_outside_window_skipped(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    entries = _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1", ts=NOW - 10 * 86400) + (
        _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r2", ts=NOW - 10 * 86400)
    )
    path = _write_session(projects, repo, entries)
    import os

    os.utime(path, (NOW, NOW))  # fresh mtime so only the per-entry gate applies
    report = scan_missed_mcp_savings(repo, projects_root=projects, days=7, now=NOW)
    assert report["events"] == 0


def test_new_session_forgets_history(repo: Path, projects: Path) -> None:
    f = str(repo / "a.py")
    _write_session(
        projects, repo, _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1"), name="s1"
    )
    _write_session(
        projects, repo, _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r2"), name="s2"
    )
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_malformed_lines_tolerated(repo: Path, projects: Path) -> None:
    good = _read_pair(str(repo / "a.py"), BIG_CONTENT, cwd=str(repo), block_id="r1") + _read_pair(
        str(repo / "a.py"), BIG_CONTENT, cwd=str(repo), block_id="r2"
    )
    path = transcript_dir_for(repo, projects) / "s1.jsonl"
    path.write_text(
        '{"tool_use" broken\n' + "\n".join(json.dumps(e) for e in good) + "\n", encoding="utf-8"
    )
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 1


def test_edit_in_same_turn_as_read_is_not_waste(repo: Path, projects: Path) -> None:
    # A Read and an Edit emitted in the SAME assistant turn share a seq; the
    # edit counts as "edited since", so a later re-read is justified, not waste.
    f = str(repo / "a.py")
    combo = {
        "type": "assistant",
        "cwd": str(repo),
        "timestamp": _iso(NOW),
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": f}},
                {"type": "tool_use", "id": "ed", "name": "Edit", "input": {"file_path": f}},
            ],
        },
    }
    combo_result = {
        "type": "user",
        "cwd": str(repo),
        "timestamp": _iso(NOW + 1),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "r1", "content": "..."}],
        },
        "toolUseResult": {"type": "text", "file": {"filePath": f, "content": BIG_CONTENT}},
    }
    entries = [combo, combo_result, *_read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r2")]
    _write_session(projects, repo, entries)
    report = scan_missed_mcp_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_read_output_text_shapes() -> None:
    from repowise.core.distill.missed_mcp import _read_output_text

    assert _read_output_text("plain string") == "plain string"
    assert _read_output_text({"type": "text", "file": {"content": "c"}}) == "c"
    assert _read_output_text({"content": "fallback"}) == "fallback"
    assert _read_output_text({"file": {"numLines": 3}}) == ""
    assert _read_output_text(None) == ""


def test_absent_transcript_dir_is_empty_report(repo: Path, tmp_path: Path) -> None:
    report = scan_missed_mcp_savings(repo, projects_root=tmp_path / "nowhere")
    assert report == {
        "events": 0,
        "raw_tokens": 0,
        "est_saved_tokens": 0,
        "per_file": {},
        "window_days": 7.0,
    }


# -- CLI surface --------------------------------------------------------------


def test_saved_missed_shows_reread_table(repo: Path, projects: Path, monkeypatch) -> None:
    f = str(repo / "a.py")
    entries = _read_pair(f, BIG_CONTENT, cwd=str(repo), block_id="r1") + _read_pair(
        f, BIG_CONTENT, cwd=str(repo), block_id="r2"
    )
    _write_session(projects, repo, entries)
    monkeypatch.setattr(
        "repowise.core.distill.missed_mcp.transcript_dir_for",
        lambda root, projects_root=None: transcript_dir_for(root, projects),
    )
    result = CliRunner().invoke(saved_command, ["--missed", str(repo)])
    assert result.exit_code == 0
    assert "file re-reads" in result.output
    assert "Estimated foregone" in result.output

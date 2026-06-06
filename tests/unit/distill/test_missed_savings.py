"""Missed-savings discovery — transcript scan, gates, and the CLI surface.

Transcript lines follow the Claude Code JSONL shape captured from real
sessions on this machine: assistant entries carry ``message.content[]``
``tool_use`` blocks plus top-level ``cwd``/``timestamp``; the paired user
entry carries ``message.content[].tool_result`` and a top-level
``toolUseResult`` with ``stdout``/``stderr``.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.saved_cmd import saved_command
from repowise.core.distill.missed import (
    RATIO_FLOOR,
    scan_missed_savings,
    transcript_dir_for,
)

NOW = time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _tool_pair(
    command: str,
    stdout: str,
    *,
    cwd: str,
    tool: str = "Bash",
    block_id: str = "toolu_01",
    ts: float = NOW,
) -> list[dict]:
    return [
        {
            "type": "assistant",
            "cwd": cwd,
            "timestamp": _iso(ts),
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": block_id,
                        "name": tool,
                        "input": {"command": command},
                    }
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
            "toolUseResult": {"stdout": stdout, "stderr": ""},
        },
    ]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "myrepo"
    (root / ".repowise").mkdir(parents=True)
    return root


@pytest.fixture()
def projects(tmp_path: Path, repo: Path) -> Path:
    """A projects root holding the munged transcript dir for *repo*."""
    root = tmp_path / "projects"
    transcript_dir_for(repo, root).mkdir(parents=True)
    return root


def _write_session(projects: Path, repo: Path, entries: list[dict], name: str = "s1") -> Path:
    path = transcript_dir_for(repo, projects) / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


#: A pytest run noisy enough to clear min_lines and the net-positive floor.
PYTEST_OUT = (
    "=" * 20 + " test session starts " + "=" * 20 + "\n" + ("tests/test_x.py::t PASSED\n" * 60)
)


def test_transcript_dir_munging() -> None:
    assert (
        transcript_dir_for(Path(r"C:\Users\x\Desktop\repo"), Path("/p")).name
        == "C--Users-x-Desktop-repo"
    )


def test_counts_classifiable_raw_command(repo: Path, projects: Path) -> None:
    _write_session(projects, repo, _tool_pair("pytest -q", PYTEST_OUT, cwd=str(repo)))
    report = scan_missed_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 1
    assert report["per_filter"]["test_output"]["events"] == 1
    expected = int(report["per_filter"]["test_output"]["raw_tokens"] * RATIO_FLOOR["test_output"])
    assert report["est_saved_tokens"] == expected


def test_powershell_tool_counts_too(repo: Path, projects: Path) -> None:
    _write_session(
        projects, repo, _tool_pair("pytest -q", PYTEST_OUT, cwd=str(repo), tool="PowerShell")
    )
    report = scan_missed_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 1


def test_distill_prefixed_commands_are_not_missed(repo: Path, projects: Path) -> None:
    _write_session(
        projects, repo, _tool_pair("repowise distill pytest -q", PYTEST_OUT, cwd=str(repo))
    )
    report = scan_missed_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_unclassifiable_command_skipped(repo: Path, projects: Path) -> None:
    _write_session(projects, repo, _tool_pair("docker compose up", "y\n" * 80, cwd=str(repo)))
    report = scan_missed_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_small_output_below_threshold_skipped(repo: Path, projects: Path) -> None:
    _write_session(projects, repo, _tool_pair("pytest -q", "ok\n", cwd=str(repo)))
    report = scan_missed_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_other_cwd_skipped(repo: Path, projects: Path) -> None:
    _write_session(projects, repo, _tool_pair("pytest -q", PYTEST_OUT, cwd=r"C:\elsewhere"))
    report = scan_missed_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 0


def test_old_entries_outside_window_skipped(repo: Path, projects: Path) -> None:
    entries = _tool_pair("pytest -q", PYTEST_OUT, cwd=str(repo), ts=NOW - 10 * 86400)
    path = _write_session(projects, repo, entries)
    # Keep the file mtime fresh so only the per-entry timestamp gate applies.
    import os

    os.utime(path, (NOW, NOW))
    report = scan_missed_savings(repo, projects_root=projects, days=7, now=NOW)
    assert report["events"] == 0


def test_malformed_lines_and_files_are_tolerated(repo: Path, projects: Path) -> None:
    good = _tool_pair("pytest -q", PYTEST_OUT, cwd=str(repo))
    path = transcript_dir_for(repo, projects) / "s1.jsonl"
    path.write_text(
        '{"tool_use" broken json "command"\n'
        + json.dumps(good[0])
        + "\n"
        + "not json at all toolUseResult\n"
        + json.dumps(good[1])
        + "\n",
        encoding="utf-8",
    )
    report = scan_missed_savings(repo, projects_root=projects, now=NOW + 10)
    assert report["events"] == 1


def test_absent_transcript_dir_is_empty_report(repo: Path, tmp_path: Path) -> None:
    report = scan_missed_savings(repo, projects_root=tmp_path / "nowhere")
    assert report == {
        "events": 0,
        "raw_tokens": 0,
        "est_saved_tokens": 0,
        "per_filter": {},
        "window_days": 7.0,
    }


def test_ratio_floors_cover_all_registered_filters() -> None:
    from repowise.core.distill.registry import filter_registry

    assert set(RATIO_FLOOR) == {f.name for f in filter_registry.filters()}


# -- CLI surface --------------------------------------------------------------


def test_saved_missed_empty_report_message(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(saved_command, ["--missed"])
    assert result.exit_code == 0
    assert "No missed savings" in result.output


def test_saved_missed_table(repo: Path, projects: Path, monkeypatch) -> None:
    _write_session(projects, repo, _tool_pair("pytest -q", PYTEST_OUT, cwd=str(repo)))
    monkeypatch.setattr(
        "repowise.core.distill.missed.transcript_dir_for",
        lambda root, projects_root=None: transcript_dir_for(root, projects),
    )
    result = CliRunner().invoke(saved_command, ["--missed", str(repo)])
    assert result.exit_code == 0
    assert "test_output" in result.output
    assert "Estimated foregone" in result.output
    flat = " ".join(result.output.split())
    assert "nothing leaves this machine" in flat

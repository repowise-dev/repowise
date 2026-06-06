"""Correction mining — fail→fixed pairing, classification, the managed block.

Transcript lines follow the Claude Code JSONL shapes captured from real
sessions on this machine: a *successful* shell call's ``toolUseResult`` is a
dict with ``stdout``/``stderr``; a *failed* call's is a **string** starting
``Error: Exit code N`` (cancellations, rejections, and permission denials are
strings too but are not command failures).
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.corrections_cmd import corrections_command
from repowise.core.distill.corrections import (
    CORRECTIONS_MARKER_START,
    command_anchor,
    render_corrections_block,
    scan_corrections,
    strip_preamble,
    update_corrections_block,
)
from repowise.core.distill.missed import transcript_dir_for

NOW = time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _shell_call(
    command: str,
    result,
    *,
    cwd: str,
    block_id: str,
    ts: float = NOW,
    tool: str = "PowerShell",
) -> list[dict]:
    """One tool_use + tool_result pair in the real transcript shape."""
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
            "toolUseResult": result,
        },
    ]


def _fail(command: str, error_tail: str = "", *, cwd: str, block_id: str, ts: float = NOW):
    return _shell_call(
        command, f"Error: Exit code 1\n{error_tail}", cwd=cwd, block_id=block_id, ts=ts
    )


def _ok(command: str, *, cwd: str, block_id: str, ts: float = NOW):
    return _shell_call(
        command,
        {"stdout": "fine", "stderr": "", "interrupted": False},
        cwd=cwd,
        block_id=block_id,
        ts=ts,
    )


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


def _scan(repo: Path, projects: Path, **kwargs):
    return scan_corrections(repo, projects_root=projects, **kwargs)


# ---------------------------------------------------------------------------
# The exit-criteria case: bare python fixed to the venv interpreter
# ---------------------------------------------------------------------------


class TestWrongTool:
    def test_bare_python_fixed_to_venv_python(self, repo: Path, projects: Path) -> None:
        entries = [
            *_fail(
                "python -m pytest tests/unit -q",
                "ModuleNotFoundError: No module named 'repowise'",
                cwd=str(repo),
                block_id="t1",
            ),
            *_ok(r".venv\Scripts\python.exe -m pytest tests/unit -q", cwd=str(repo), block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        report = _scan(repo, projects)
        assert report["pairs"] == 1
        (rule,) = report["rules"]
        assert rule["kind"] == "wrong_tool"
        assert rule["wrong"] == "python"
        assert rule["fixed"] == r".venv\Scripts\python.exe"
        assert rule["example"]["failed"].startswith("python -m pytest")

    def test_preamble_and_runner_form_still_pair(self, repo: Path, projects: Path) -> None:
        # Real shape: env preamble + module runner on one side, plain exe on
        # the other. The anchor must survive both differences.
        entries = [
            *_fail("pytest tests/unit -q", "errors", cwd=str(repo), block_id="t1"),
            *_ok(
                r'$env:PYTHONPATH="C:\x"; C:\repo\.venv\Scripts\python.exe -m pytest tests/unit -q',
                cwd=str(repo),
                block_id="t2",
            ),
        ]
        _write_session(projects, repo, entries)
        report = _scan(repo, projects)
        assert report["pairs"] == 1
        assert report["rules"][0]["kind"] == "wrong_tool"

    def test_multiline_command_still_classifies_wrong_tool(
        self, repo: Path, projects: Path
    ) -> None:
        entries = [
            *_fail(
                "python - <<'PY'\nimport repowise\nPY",
                "No module named 'repowise'",
                cwd=str(repo),
                block_id="t1",
            ),
            *_ok(
                ".venv/Scripts/python.exe - <<'PY'\nimport repowise\nPY",
                cwd=str(repo),
                block_id="t2",
            ),
        ]
        _write_session(projects, repo, entries)
        report = _scan(repo, projects)
        assert report["pairs"] == 1
        assert report["rules"][0]["kind"] == "wrong_tool"


# ---------------------------------------------------------------------------
# Precision: things that must NOT become corrections
# ---------------------------------------------------------------------------


class TestPrecisionGuards:
    def test_identical_retry_is_flaky_not_a_correction(self, repo: Path, projects: Path) -> None:
        entries = [
            *_fail("pytest tests/unit -q", "1 failed", cwd=str(repo), block_id="t1"),
            *_ok("pytest tests/unit -q", cwd=str(repo), block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        assert _scan(repo, projects)["pairs"] == 0

    def test_red_green_loop_is_not_a_correction(self, repo: Path, projects: Path) -> None:
        # Failing tests, then a re-run with a different selection once the
        # code was fixed — exit 1 came from the tests, not the command.
        entries = [
            *_fail(
                "pytest tests/unit/test_a.py -q",
                "FAILED tests/unit/test_a.py::test_x - AssertionError",
                cwd=str(repo),
                block_id="t1",
            ),
            *_ok(
                "pytest tests/unit/test_a.py tests/unit/test_b.py -q", cwd=str(repo), block_id="t2"
            ),
        ]
        _write_session(projects, repo, entries)
        assert _scan(repo, projects)["pairs"] == 0

    def test_cancelled_and_rejected_results_are_not_failures(
        self, repo: Path, projects: Path
    ) -> None:
        entries = [
            *_shell_call(
                "pytest -q", "Cancelled: parallel tool call Bash(x)", cwd=str(repo), block_id="t1"
            ),
            *_shell_call("pytest -q", "User rejected tool use", cwd=str(repo), block_id="t2"),
            *_ok(".venv/Scripts/pytest.exe -q", cwd=str(repo), block_id="t3"),
        ]
        _write_session(projects, repo, entries)
        assert _scan(repo, projects)["pairs"] == 0

    def test_different_anchor_never_pairs(self, repo: Path, projects: Path) -> None:
        entries = [
            *_fail("pytest tests -q", "boom", cwd=str(repo), block_id="t1"),
            *_ok("git status", cwd=str(repo), block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        assert _scan(repo, projects)["pairs"] == 0

    def test_echo_strategy_changes_are_ignored(self, repo: Path, projects: Path) -> None:
        entries = [
            *_fail("echo recovered; exit 1", "recovered", cwd=str(repo), block_id="t1"),
            *_ok("echo alive", cwd=str(repo), block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        assert _scan(repo, projects)["pairs"] == 0

    def test_outside_repo_cwd_is_skipped(self, repo: Path, projects: Path, tmp_path: Path) -> None:
        other = str(tmp_path / "elsewhere")
        entries = [
            *_fail("python x.py", "No module named x", cwd=other, block_id="t1"),
            *_ok(".venv/Scripts/python.exe x.py", cwd=other, block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        assert _scan(repo, projects)["pairs"] == 0

    def test_old_events_age_out_of_the_window(self, repo: Path, projects: Path) -> None:
        old = NOW - 90 * 86400
        entries = [
            *_fail("python x.py", "No module named x", cwd=str(repo), block_id="t1", ts=old),
            *_ok(".venv/Scripts/python.exe x.py", cwd=str(repo), block_id="t2", ts=old + 2),
        ]
        path = _write_session(projects, repo, entries)
        # The mtime prefilter alone would skip the file; pin mtime to now so
        # the per-entry timestamp cutoff is what's exercised.
        import os

        os.utime(path, (NOW, NOW))
        assert _scan(repo, projects, days=30)["pairs"] == 0


# ---------------------------------------------------------------------------
# The other kinds — error-corroborated classification
# ---------------------------------------------------------------------------


class TestKinds:
    def test_wrong_path_requires_error_naming_the_path(self, repo: Path, projects: Path) -> None:
        entries = [
            *_fail(
                "pytest ../../tests/unit/cli/ -x -q",
                "ERROR: file or directory not found: ../../tests/unit/cli/",
                cwd=str(repo),
                block_id="t1",
            ),
            *_ok("pytest tests/unit/cli/ -x -q", cwd=str(repo), block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        report = _scan(repo, projects)
        assert report["pairs"] == 1
        (rule,) = report["rules"]
        assert rule["kind"] == "wrong_path"
        assert rule["wrong"] == "../../tests/unit/cli/"
        assert rule["fixed"] == "tests/unit/cli/"

    def test_unknown_flag_requires_error_naming_the_flag(self, repo: Path, projects: Path) -> None:
        entries = [
            *_fail(
                "pytest tests -q --looponfail",
                "ERROR: unrecognized arguments: --looponfail",
                cwd=str(repo),
                block_id="t1",
            ),
            *_ok("pytest tests -q", cwd=str(repo), block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        report = _scan(repo, projects)
        assert report["pairs"] == 1
        (rule,) = report["rules"]
        assert rule["kind"] == "unknown_flag"
        assert rule["wrong"] == "--looponfail"

    def test_repeated_fumbles_aggregate_by_count(self, repo: Path, projects: Path) -> None:
        entries = []
        for i in range(3):
            entries += _fail(
                "python -m pytest tests -q",
                "No module named 'pytest'",
                cwd=str(repo),
                block_id=f"f{i}",
            )
            entries += _ok(
                r".venv\Scripts\python.exe -m pytest tests -q", cwd=str(repo), block_id=f"s{i}"
            )
        _write_session(projects, repo, entries)
        report = _scan(repo, projects)
        assert report["pairs"] == 3
        assert report["rules"][0]["count"] == 3

    def test_wrong_path_hint_consults_the_index(self, repo: Path, projects: Path) -> None:
        db = repo / ".repowise" / "wiki.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE wiki_symbols (file_path TEXT)")
            conn.execute(
                "INSERT INTO wiki_symbols VALUES ('tests/unit/health/test_coverage_parsers.py')"
            )
        entries = [
            *_fail(
                "pytest ../../tests/unit/health/test_coverage_parsers.py -q",
                "ERROR: file or directory not found: ../../tests/unit/health/test_coverage_parsers.py",
                cwd=str(repo),
                block_id="t1",
            ),
            *_ok(
                "pytest tests/unit/health/test_coverage_parsers.py -q", cwd=str(repo), block_id="t2"
            ),
        ]
        _write_session(projects, repo, entries)
        (rule,) = _scan(repo, projects)["rules"]
        assert rule["hint"] == "indexed at tests/unit/health/test_coverage_parsers.py"


# ---------------------------------------------------------------------------
# Robustness contract
# ---------------------------------------------------------------------------


class TestBestEffort:
    def test_missing_transcript_dir_is_empty_report(self, repo: Path, tmp_path: Path) -> None:
        report = scan_corrections(repo, projects_root=tmp_path / "nope")
        assert report == {"rules": [], "pairs": 0, "window_days": 30.0}

    def test_malformed_lines_are_tolerated(self, repo: Path, projects: Path) -> None:
        path = transcript_dir_for(repo, projects) / "bad.jsonl"
        path.write_text('{"tool_use" "command" broken\nnot json\n', encoding="utf-8")
        assert _scan(repo, projects)["pairs"] == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize(
        ("command", "anchor"),
        [
            ("python -m pytest tests -q", "pytest"),
            (r"C:\repo\.venv\Scripts\python.exe -m pytest tests", "pytest"),
            ("python script.py", "python"),
            (r'$env:X="1"; & "C:\tools\rg.exe" TODO', "rg"),
            ("Set-Location C:\\repo; git status", "git"),
            ("cd /tmp && ls -la", "ls"),
            ("", ""),
        ],
    )
    def test_command_anchor(self, command: str, anchor: str) -> None:
        assert command_anchor(command) == anchor

    def test_strip_preamble_keeps_the_real_command(self) -> None:
        cmd = '$env:PYTHONIOENCODING="utf-8"; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; pytest -q'
        assert strip_preamble(cmd) == "pytest -q"


# ---------------------------------------------------------------------------
# The managed --write block
# ---------------------------------------------------------------------------


def _rule(count: int = 3) -> dict:
    return {
        "kind": "wrong_tool",
        "anchor": "pytest",
        "wrong": "python",
        "fixed": r".venv\Scripts\python.exe",
        "count": count,
        "example": {"failed": "python -m pytest", "fixed": r".venv\Scripts\python.exe -m pytest"},
    }


class TestManagedBlock:
    def test_render_respects_threshold_and_cap(self) -> None:
        rules = [_rule(5), _rule(1)]
        block = render_corrections_block(rules, min_count=2)
        assert block is not None
        assert block.count("\n- ") == 1
        assert "instead of `python`" in block
        assert render_corrections_block([_rule(1)], min_count=2) is None

    def test_update_round_trips_user_content(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        original = "# CLAUDE.md\n\nMy rules.\n"
        target.write_text(original, encoding="utf-8", newline="\n")

        block = render_corrections_block([_rule()])
        assert update_corrections_block(target, block) is True
        content = target.read_text(encoding="utf-8")
        assert content.startswith("# CLAUDE.md\n\nMy rules.")
        assert CORRECTIONS_MARKER_START in content

        # Refresh in place, no duplication.
        assert update_corrections_block(target, block) is False
        assert target.read_text(encoding="utf-8").count(CORRECTIONS_MARKER_START) == 1

        # Removal restores the original bytes.
        assert update_corrections_block(target, None) is True
        assert target.read_text(encoding="utf-8") == original

    def test_remove_never_creates_a_file(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        assert update_corrections_block(target, None) is False
        assert not target.exists()


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    @pytest.fixture(autouse=True)
    def _redirect_transcripts(self, projects: Path, monkeypatch) -> None:
        from repowise.core.distill import corrections as core

        monkeypatch.setattr(
            core, "transcript_dir_for", lambda root, _=None: transcript_dir_for(root, projects)
        )

    def test_report_only_by_default(self, repo: Path, projects: Path, monkeypatch) -> None:
        entries = [
            *_fail("python -m pytest -q", "No module named 'pytest'", cwd=str(repo), block_id="t1"),
            *_ok(r".venv\Scripts\python.exe -m pytest -q", cwd=str(repo), block_id="t2"),
        ]
        _write_session(projects, repo, entries)
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(corrections_command, [])
        assert result.exit_code == 0
        assert "wrong tool" in result.output
        assert not (repo / ".claude" / "CLAUDE.md").exists()  # report-only

    def test_write_maintains_block_in_claude_md(
        self, repo: Path, projects: Path, monkeypatch
    ) -> None:
        entries = []
        for i in range(2):
            entries += _fail(
                "python -m pytest -q", "No module named 'pytest'", cwd=str(repo), block_id=f"f{i}"
            )
            entries += _ok(
                r".venv\Scripts\python.exe -m pytest -q", cwd=str(repo), block_id=f"s{i}"
            )
        _write_session(projects, repo, entries)
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(corrections_command, ["--write"])
        assert result.exit_code == 0
        content = (repo / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Known command corrections" in content
        assert r".venv\Scripts\python.exe" in content

    def test_no_findings_message(self, repo: Path, projects: Path, monkeypatch) -> None:
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(corrections_command, [])
        assert result.exit_code == 0
        assert "No command corrections" in result.output

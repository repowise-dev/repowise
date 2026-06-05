"""Unit tests for the ``repowise distill`` / ``repowise expand`` commands."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.distill_cmd import distill_command
from repowise.cli.commands.expand_cmd import expand_command
from repowise.core.distill.markers import parse_marker_refs
from repowise.core.distill.store import OmissionStore


@pytest.fixture()
def repo_cwd(tmp_path: Path, monkeypatch) -> Path:
    """A scratch repo with .repowise/ so the store lands locally."""
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_distill_preserves_exit_code(repo_cwd: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(distill_command, _py("import sys; sys.exit(7)"))
    assert result.exit_code == 7


def test_distill_unmatched_command_passes_output_through(repo_cwd: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(distill_command, _py("print('plain output')"))
    assert result.exit_code == 0
    assert "plain output" in result.output
    assert "[repowise#" not in result.output


def test_distill_captures_stderr_too(repo_cwd: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        distill_command,
        _py("import sys; print('out'); print('err line', file=sys.stderr)"),
    )
    assert "out" in result.output
    assert "err line" in result.output


def test_distill_failing_git_command_keeps_exit_code(repo_cwd: Path) -> None:
    # tmp dir is not a git repository: git fails, the engine passes the raw
    # error output through, and the nonzero exit code survives.
    result = CliRunner().invoke(distill_command, ["git", "log", "-40"])
    assert result.exit_code != 0


def test_distill_and_expand_roundtrip(repo_cwd: Path, fixtures_dir: Path) -> None:
    raw = (fixtures_dir / "distill" / "git_log_full.txt").read_text(encoding="utf-8-sig")
    from repowise.core.distill import distill_output

    store = OmissionStore(repo_cwd / ".repowise" / "omissions" / "omissions.db")
    distilled = distill_output(raw, command="git log -40", store=store)
    store.close()
    assert distilled.distilled
    (ref,) = parse_marker_refs(distilled.text)

    result = CliRunner().invoke(expand_command, [ref])
    assert result.exit_code == 0
    assert result.output.rstrip("\n") == raw.rstrip("\n")


def test_expand_accepts_pasted_marker(repo_cwd: Path) -> None:
    store = OmissionStore(repo_cwd / ".repowise" / "omissions" / "omissions.db")
    ref = store.put("stashed content", source="cli:logs", original_tokens=10, kept_tokens=2)
    store.close()
    marker = f"[repowise#{ref}: 3 lines omitted (~30 tokens); restore: repowise expand {ref}]"
    result = CliRunner().invoke(expand_command, [marker])
    assert result.exit_code == 0
    assert "stashed content" in result.output


def test_expand_with_query_filters_lines(repo_cwd: Path) -> None:
    store = OmissionStore(repo_cwd / ".repowise" / "omissions" / "omissions.db")
    ref = store.put(
        "keep FAILED a\ndrop ok b\nkeep FAILED c", source="cli:t", original_tokens=9, kept_tokens=1
    )
    store.close()
    result = CliRunner().invoke(expand_command, [ref, "--query", "FAILED"])
    assert result.exit_code == 0
    assert "drop ok b" not in result.output
    assert "keep FAILED a" in result.output


def test_expand_unknown_ref_fails_cleanly(repo_cwd: Path) -> None:
    result = CliRunner().invoke(expand_command, ["0" * 12])
    assert result.exit_code == 1


def test_expand_invalid_ref_fails_cleanly(repo_cwd: Path) -> None:
    result = CliRunner().invoke(expand_command, ["zzz"])
    assert result.exit_code == 2


def test_distill_records_savings_ledger(repo_cwd: Path, fixtures_dir: Path) -> None:
    fixture = fixtures_dir / "distill" / "find_paths.txt"
    raw = fixture.read_text(encoding="utf-8-sig")
    from repowise.core.distill import distill_output

    store = OmissionStore(repo_cwd / ".repowise" / "omissions" / "omissions.db")
    result = distill_output(raw, command="find packages -name *.py", source="cli", store=store)
    assert result.distilled
    summary = store.savings_summary()
    store.close()
    assert summary["per_filter"]["file_listing"]["events"] == 1

"""Unit tests for the ``repowise distill`` / ``repowise expand`` commands."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.distill_cmd import _render_command, distill_command
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


@pytest.mark.skipif(os.name != "posix", reason="cmd.exe has no grep")
def test_distill_runs_a_pipeline_passed_as_one_token(repo_cwd: Path) -> None:
    """One argv token containing a pipe must execute AS a pipeline.

    This is the execution model the rewrite hook's safe-pipeline shape
    depends on: the hook quotes the whole pipeline so the pipe binds inside
    distill's own shell. The producer prints a line only grep can remove, so
    the assertion fails if the pipe silently did not run.
    """
    script = (
        "for i in range(40): "
        "print('Requirement already satisfied: pkg%d in /venv/site-packages' % i)\n"
        "print('ZZZDROPME')\n"
        "print('Successfully installed flask-3.1.0')"
    )
    pipeline = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} | grep -v ZZZDROPME"

    result = CliRunner().invoke(distill_command, [pipeline])

    assert result.exit_code == 0
    # Proof the pipe ran: only grep could have dropped this line.
    assert "ZZZDROPME" not in result.output
    # Proof distill still distilled what came back out of it.
    assert "Successfully installed flask-3.1.0" in result.output
    refs = parse_marker_refs(result.output)
    assert refs, f"no recoverable marker in:\n{result.output}"
    assert CliRunner().invoke(expand_command, [refs[0]]).exit_code == 0


def test_render_command_quotes_shell_metacharacters() -> None:
    """argv tokens must not turn into shell syntax when distill rejoins them.

    The rewrite hook leans on this for every non-pipe command it forwards as
    separate tokens: ``pytest -k "a|b"`` must run as one command, not a
    pipeline. POSIX ``shlex.join`` gets it right. Windows
    ``list2cmdline`` quotes for the C runtime's argv parser rather than for
    cmd.exe, so a metacharacter with no surrounding space rides through
    unquoted; ``_render_command`` caret-escapes the rendered line to close
    that.
    """
    rendered = _render_command(("git", "log", "--grep=a&&b"))
    if os.name == "posix":
        assert rendered == "git log '--grep=a&&b'"
    else:
        assert rendered == "git log --grep=a^&^&b"


def test_render_command_passes_a_single_token_through() -> None:
    """One token means the user quoted the command themselves; that is intent."""
    assert _render_command(("pytest -x | head -5",)) == "pytest -x | head -5"


@pytest.mark.parametrize(
    "payload",
    [
        "--grep=a&whoami",
        "--grep=a|whoami",
        "--grep=a>rendered_should_not_write.txt",
        "--grep=a;b",
        "plain",
        "has space",
        'say "hi" now',
        'a"b&c',
        "a^b",
        "a(b)c",
        "trailing\\back\\slash\\",
        "--format=%H",
    ],
)
def test_render_command_roundtrips_argv_through_the_shell(payload: str, tmp_path: Path) -> None:
    """The rendered string must hand the child exactly the token we started with.

    This is the real contract: ``_render_command`` output goes straight to
    ``subprocess.run(..., shell=True)``, so anything the shell reinterprets
    is a command the user never typed. Asserting on the rendered string
    alone did not catch the cmd.exe metacharacter hole, so this one runs the
    render and reads the child's ``sys.argv`` back.
    """
    tokens = (sys.executable, "-c", "import sys,json;print(json.dumps(sys.argv[1:]))", payload)
    proc = subprocess.run(
        _render_command(tokens),
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == [payload]
    # A redirect that reached the shell would have written a file instead.
    assert list(tmp_path.iterdir()) == []


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


def test_distill_source_flag_tags_the_ledger(repo_cwd: Path) -> None:
    """The hook's --source tag flows into the ledger for `saved --by source`."""
    runner = CliRunner()
    noisy = "import sys; sys.stdout.write('x.py:1:1: E501 line too long\\n' * 80)"
    result = runner.invoke(distill_command, ["--source", "hook-powershell", *_py(noisy)])
    assert result.exit_code == 0
    store = OmissionStore(repo_cwd / ".repowise" / "omissions" / "omissions.db")
    rows = store.savings_rollup(by="source")
    store.close()
    assert [r["group"] for r in rows] == ["hook-powershell"]


def test_distill_expand_roundtrip_from_codex_surface(repo_cwd: Path) -> None:
    """The omission store and `expand` are agent-agnostic — a marker produced
    under the Codex hook's source tag restores exactly like any other."""
    runner = CliRunner()
    noisy = "import sys; sys.stdout.write('x.py:1:1: E501 line too long\\n' * 80)"
    result = runner.invoke(distill_command, ["--source", "hook-codex", *_py(noisy)])
    assert result.exit_code == 0
    (ref,) = parse_marker_refs(result.output)

    expanded = CliRunner().invoke(expand_command, [ref])
    assert expanded.exit_code == 0
    assert expanded.output.count("E501 line too long") == 80

    store = OmissionStore(repo_cwd / ".repowise" / "omissions" / "omissions.db")
    rows = store.savings_rollup(by="source")
    store.close()
    assert [r["group"] for r in rows] == ["hook-codex"]


def test_distill_does_not_eat_the_wrapped_commands_source_flag(repo_cwd: Path) -> None:
    """--source after the command belongs to the command, not to distill."""
    result = CliRunner().invoke(
        distill_command,
        _py("import sys; print(sys.argv[1:])") + ["--source", "mine"],
    )
    assert result.exit_code == 0
    assert "['--source', 'mine']" in result.output


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

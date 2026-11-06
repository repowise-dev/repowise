"""The opt-in PreToolUse health gate on agent ``git commit`` / ``git push``."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli import health_gate_hook as gate
from repowise.cli.commands.hook_cmd import hook_group

_NESTED = "def bad(x):\n" + "".join(
    "    " * (depth + 1) + f"if x > {depth}:\n" for depth in range(9)
) + "    " * 10 + "return x\n    return 0\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / ".repowise").mkdir()
    (root / "a.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "a.py")
    _git(root, "commit", "-qm", "base")
    return root


def _enable(repo: Path) -> None:
    (repo / ".repowise" / "config.yaml").write_text("hooks:\n  health_gate: true\n", "utf-8")


def _payload(command: str, cwd: Path, tool: str = "Bash") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }


def _no_health_check(monkeypatch) -> list:
    """Record health-check calls instead of running them."""
    calls: list = []
    monkeypatch.setattr(
        gate, "_run_health_check", lambda root, revspec, env, deadline: calls.append(revspec) or []
    )
    return calls


# -- reading the command ------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git commit -m x", [("commit", "/r")]),
        ("git add a.py && git commit -m 'a && b'", [("add", "/r"), ("commit", "/r")]),
        ("git -C sub push", [("push", "sub")]),
        ("cd sub && git commit -am x", [("commit", "sub")]),
        ("FOO=1 git push origin HEAD 2>&1 | tail -5", [("push", "/r"), ("(shell)", "/r")]),
        ('repowise distill "git push"', [("push", "/r")]),
        ("echo git commit", []),
        ("git log --grep commit", [("log", "/r")]),
        ("git commit -m 'unbalanced", []),
        ("cat <<EOF\ngit commit\nEOF", []),
        ("(cd sub && git commit -m x)", []),
        ("ruff check . && git commit -m x", [("(shell)", "/r"), ("commit", "/r")]),
        ("echo hi && git commit -m x", [("commit", "/r")]),
    ],
)
def test_git_calls_reads_verbs_and_directories(command, expected) -> None:
    calls = gate.git_calls(command, "/r")
    assert [(c.verb, Path(c.cwd).name if c.cwd != "/r" else "/r") for c in calls] == expected


def test_git_dir_override_is_marked_opaque() -> None:
    assert gate.git_calls("git --git-dir=x commit -m y", "/r")[0].opaque
    assert gate.git_calls("GIT_INDEX_FILE=x git commit -m y", "/r")[0].opaque
    assert not gate.git_calls("LANG=C git commit -m y", "/r")[0].opaque


def test_a_quoted_heredoc_message_is_still_read() -> None:
    command = "git commit -m \"$(cat <<'EOF'\nfix: x\n\nbody\nEOF\n)\""
    assert [c.verb for c in gate.git_calls(command, "/r")] == ["commit"]


@pytest.mark.parametrize(
    ("args", "plan"),
    [
        (("-m", "x"), "staged"),
        (("-am", "x"), "all"),
        (("--all", "--message", "x"), "all"),
        (("-mx", "--no-verify"), "staged"),
        (("--amend", "--no-edit"), None),
        (("-m", "x", "a.py"), None),
        (("-p",), None),
        (("--only", "-m", "x"), None),
    ],
)
def test_commit_plan(args, plan) -> None:
    assert gate.commit_plan(args) == plan


@pytest.mark.parametrize(
    ("args", "reads"),
    [
        ((), True),
        (("-u", "origin", "HEAD"), True),
        (("origin", "main"), True),
        (("origin", "+main:main"), True),
        (("origin", "other"), False),
        (("--tags",), False),
        (("origin", "--delete", "x"), False),
        (("origin", ":main"), False),
        (("--dry-run",), False),
        (("origin", "HEAD:refs/heads/other"), False),
        (("origin", "HEAD:main"), True),
    ],
)
def test_push_targets_head(args, reads) -> None:
    assert gate.push_targets_head(args, "main") is reads


# -- off by default and failing open ------------------------------------------


def test_off_without_the_config_key(repo, monkeypatch) -> None:
    calls = _no_health_check(monkeypatch)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    _git(repo, "add", "b.py")
    assert gate.deny_reason(_payload("git commit -m x", repo)) is None
    assert calls == []


def test_init_consent_never_turns_it_on() -> None:
    from repowise.cli.helpers import HOOK_REPLACEMENT_SURFACES

    assert "health_gate" not in HOOK_REPLACEMENT_SURFACES


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "git push"}},
        {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"command": "git push"}},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": ["git"]}},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}},
    ],
)
def test_non_gated_payloads_pass(payload, tmp_path) -> None:
    assert gate.deny_reason({**payload, "cwd": str(tmp_path)}) is None


def test_bad_stdin_writes_nothing() -> None:
    out = io.StringIO()
    gate.run(io.StringIO("not json"), out)
    gate.run(io.StringIO("[1]"), out)
    assert out.getvalue() == ""


def test_not_a_git_repository_passes(tmp_path, monkeypatch) -> None:
    calls = _no_health_check(monkeypatch)
    (tmp_path / ".repowise").mkdir()
    _enable(tmp_path)
    assert gate.deny_reason(_payload("git commit -m x", tmp_path)) is None
    assert calls == []


@pytest.mark.parametrize("returncode", [0, 2])
def test_only_exit_1_blocks(repo, monkeypatch, returncode) -> None:
    _enable(repo)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    _git(repo, "add", "b.py")
    real_run = subprocess.run

    def fake(argv, *args, **kwargs):
        if argv[:3] == [sys.executable, "-m", "repowise.cli.main"]:
            return subprocess.CompletedProcess(argv, returncode, '{"gate": {"failing": [{}]}}', "")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(gate.subprocess, "run", fake)
    assert gate.deny_reason(_payload("git commit -m x", repo)) is None


def test_timeout_and_crash_pass(repo, monkeypatch) -> None:
    _enable(repo)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    _git(repo, "add", "b.py")
    real_run = subprocess.run

    def slow(argv, *args, **kwargs):
        if argv[:3] == [sys.executable, "-m", "repowise.cli.main"]:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(gate.subprocess, "run", slow)
    assert gate.deny_reason(_payload("git commit -m x", repo)) is None
    monkeypatch.setattr(gate, "gate", lambda *a, **k: 1 / 0)
    assert gate.deny_reason(_payload("git commit -m x", repo)) is None


def test_an_exhausted_budget_skips_the_check(repo, monkeypatch) -> None:
    _enable(repo)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    _git(repo, "add", "b.py")
    assert gate.gate("git commit -m x", str(repo), budget=0) is None


def test_unread_commit_forms_pass_without_a_check(repo, monkeypatch) -> None:
    _enable(repo)
    calls = _no_health_check(monkeypatch)
    for command in (
        "git commit --amend --no-edit",
        "git commit -m x a.py",
        "git rm a.py && git commit -m x",
        "git commit -m x",  # nothing staged
        "npm run format && git add a.py && git commit -m x",
    ):
        assert gate.deny_reason(_payload(command, repo)) is None
    assert calls == []


# -- what is checked ----------------------------------------------------------


def test_commit_checks_the_chain_s_adds_and_leaves_the_repo_alone(repo, monkeypatch) -> None:
    _enable(repo)
    calls = _no_health_check(monkeypatch)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    objects_before = _git(repo, "count-objects", "-v")
    gate.deny_reason(_payload("git add b.py && git commit -m x", repo))
    assert len(calls) == 1 and calls[0].startswith(_git(repo, "rev-parse", "HEAD") + "..")
    # The snapshot lived in a scratch index and object store.
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert _git(repo, "count-objects", "-v") == objects_before


def test_commit_all_picks_up_tracked_edits(repo, monkeypatch) -> None:
    _enable(repo)
    calls = _no_health_check(monkeypatch)
    (repo / "a.py").write_text(_NESTED, encoding="utf-8")
    gate.deny_reason(_payload("git commit -m x", repo))
    assert calls == []  # nothing staged
    gate.deny_reason(_payload("git commit -am x", repo))
    assert len(calls) == 1


def test_push_compares_with_the_upstream(repo, monkeypatch) -> None:
    _enable(repo)
    calls = _no_health_check(monkeypatch)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", str(repo))
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "branch", "-q", "--set-upstream-to=origin/main")
    gate.deny_reason(_payload("git push", repo))
    assert calls == []  # nothing to push
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    _git(repo, "add", "b.py")
    _git(repo, "commit", "-qm", "b")
    gate.deny_reason(_payload("git push origin main", repo))
    assert calls == [f"{base}...{_git(repo, 'rev-parse', 'HEAD')}"]
    gate.deny_reason(_payload("git push origin other", repo))
    assert len(calls) == 1


def test_a_failing_commit_is_denied_with_its_findings(repo) -> None:
    """End to end: the real ``repowise health check`` decides."""
    _enable(repo)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    out = io.StringIO()
    stdin = io.StringIO(json.dumps(_payload("git add b.py && git commit -m wip", repo)))
    gate.run(stdin, out)
    response = json.loads(out.getvalue())["hookSpecificOutput"]
    assert response["hookEventName"] == "PreToolUse"
    assert response["permissionDecision"] == "deny"
    assert "b.py:1" in response["permissionDecisionReason"]
    assert "repowise hook health-gate uninstall" in response["permissionDecisionReason"]


def test_the_hook_entry_imports_nothing_heavy_for_other_commands(tmp_path) -> None:
    probe = (
        "import io, json, sys; "
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(_payload('ls -la', tmp_path)))}); "
        "sys.argv = ['repowise-augment', '--health-gate']; "
        "from repowise.cli.augment_hook import main\n"
        "try:\n    main()\nexcept SystemExit:\n    pass\n"
        "print(sorted(m for m in sys.modules if m.startswith("
        "('repowise.cli.commands', 'repowise.core.analysis', 'sqlalchemy', 'structlog'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip().splitlines()[-1] == "[]"


# -- install / uninstall ------------------------------------------------------


def _local(repo: Path) -> dict:
    path = repo / ".claude" / "settings.local.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_install_and_uninstall_write_and_remove_both_halves(repo) -> None:
    from repowise.cli.commands.augment_cmd._shared import hook_flag_enabled

    foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "make lint"}]}
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [foreign]}}), encoding="utf-8"
    )
    result = CliRunner().invoke(hook_group, ["health-gate", "install", str(repo)])
    assert result.exit_code == 0, result.output
    assert hook_flag_enabled(repo, "health_gate")
    entries = _local(repo)["hooks"]["PreToolUse"]
    assert entries[0] == foreign and "--health-gate" in entries[1]["hooks"][0]["command"]

    status = CliRunner().invoke(hook_group, ["health-gate", "status", str(repo)])
    assert "health gate: on" in status.output

    CliRunner().invoke(hook_group, ["health-gate", "install", str(repo)])
    assert len(_local(repo)["hooks"]["PreToolUse"]) == 2  # idempotent

    result = CliRunner().invoke(hook_group, ["health-gate", "uninstall", str(repo)])
    assert result.exit_code == 0, result.output
    assert not hook_flag_enabled(repo, "health_gate")
    assert _local(repo) == {"hooks": {"PreToolUse": [foreign]}}


def test_hook_uninstall_removes_the_gate_too(repo) -> None:
    CliRunner().invoke(hook_group, ["health-gate", "install", str(repo)])
    result = CliRunner().invoke(hook_group, ["uninstall", str(repo)])
    assert "Health gate on agent commits and pushes" in result.output
    assert not (repo / ".claude" / "settings.local.json").exists()


def test_project_uninstall_removes_the_entry(repo) -> None:
    from repowise.cli.agent_targets.targets.claude_code import ClaudeCodeTarget
    from repowise.cli.agent_targets.types import Scope

    CliRunner().invoke(hook_group, ["health-gate", "install", str(repo)])
    ClaudeCodeTarget().uninstall(Scope.PROJECT, repo_path=repo)
    assert not (repo / ".claude" / "settings.local.json").exists()


def test_the_entry_counts_as_repowise_s_own_edit() -> None:
    from repowise.cli.editor_integrations.claude_config import _health_gate_entry
    from repowise.core.owned_paths import only_repowise_differs

    created = json.dumps({"hooks": {"PreToolUse": [_health_gate_entry()]}})
    assert only_repowise_differs(".claude/settings.local.json", "", created)


def test_a_merge_in_progress_passes_without_a_check(repo, monkeypatch) -> None:
    _enable(repo)
    calls = _no_health_check(monkeypatch)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    _git(repo, "add", "b.py")
    (repo / ".git" / "MERGE_HEAD").write_text(_git(repo, "rev-parse", "HEAD") + "\n")
    assert gate.deny_reason(_payload("git commit -m x", repo)) is None
    assert calls == []


def test_a_push_after_a_commit_in_the_chain_checks_only_the_commit(repo, monkeypatch) -> None:
    _enable(repo)
    calls = _no_health_check(monkeypatch)
    (repo / "b.py").write_text(_NESTED, encoding="utf-8")
    gate.deny_reason(_payload("git add b.py && git commit -m x && git push", repo))
    assert len(calls) == 1 and ".." in calls[0] and "..." not in calls[0]

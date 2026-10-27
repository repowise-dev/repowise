"""PostToolUse Bash: a full test run with a fresh coverage report re-ingests it in the background."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import coverage_reingest as hook
from repowise.cli.commands.augment_cmd.command import _handle_post_tool_use, _run_augment

#: When the last ingest happened, in the stored UTC shape SQLite keeps.
_INGESTED_AT = "2026-09-01 12:00:00.000000"
_INGESTED_EPOCH = 1788264000.0  # 2026-09-01T12:00:00Z


def _db(root, *ingests: str) -> None:
    (root / ".repowise").mkdir(exist_ok=True)
    con = sqlite3.connect(root / ".repowise" / "wiki.db")
    con.execute("CREATE TABLE coverage_ingests (ingested_at DATETIME NOT NULL)")
    con.executemany("INSERT INTO coverage_ingests VALUES (?)", [(i,) for i in ingests])
    con.commit()
    con.close()


#: The re-ingest is opt-in; every fixture repo opts in unless a test says otherwise.
_OPT_IN = "hooks:\n  coverage_reingest: true\n"


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("REPOWISE_HOOK_COVERAGE_REINGEST", raising=False)


@pytest.fixture
def repo(tmp_path):
    """An opted-in indexed repo whose last coverage ingest is ``_INGESTED_AT``."""
    _db(tmp_path, _INGESTED_AT)
    _config(tmp_path, "")
    return tmp_path


@pytest.fixture
def spawned(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(hook, "spawn_coverage_add", lambda root, reports: calls.append((root, reports)))
    return calls


def _report(repo, rel: str = "coverage/lcov.info", *, after: float = 60) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SF:a.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
    os.utime(path, (_INGESTED_EPOCH + after,) * 2)


def _config(repo, text: str) -> None:
    """Write *text* as the config, opted in unless *text* sets the flag itself."""
    opt_in = "" if "coverage_reingest" in text else _OPT_IN
    (repo / ".repowise" / "config.yaml").write_text(opt_in + text, encoding="utf-8")


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "pytest -q -x",
        "cd api && python -m pytest -x",
        ".venv/Scripts/python.exe -m pytest",
        "coverage run -m pytest",
        "FOO=1 time timeout 600 pytest",
        "go test ./...",
        "npm test",
        "npm run test -- --coverage",
        "npm run test:unit",
        "pnpm test",
        "yarn test",
        "bun test",
        "npx vitest run",
        "vitest",
        "jest --coverage",
        "uv run pytest; echo done",
        "poetry run pytest | tail -5",
        "cargo test",
        "cargo llvm-cov --lcov --output-path coverage/lcov.info",
        "mvn test",
        "./gradlew test",
        "repowise distill pytest -q",
        "repowise distill --source hook 'cd api && pytest -q'",
        ["pytest", "-q"],
        "pytest -p no:cacheprovider",
        "pytest -n 4 -W error",
        "go test -count 1 -coverprofile cover.out ./...",
        "go test -p 4 ./...",
        "cargo test --workspaces",
    ],
)
def test_full_test_runs_are_recognised(command) -> None:
    assert hook.is_full_test_run(command)


@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/unit/test_a.py",
        "pytest tests/test_a.py::test_one",
        "pytest -k login",
        "go test -run TestLogin ./...",
        "go test ./pkg/auth",
        "npx vitest run src/a.test.ts",
        "jest -t login",
        "jest --testNamePattern=login",
        "cargo test parse_",
        "mvn test -Dtest=AuthTest",
        "./gradlew test --tests AuthTest",
        "dotnet test --filter Auth",
        "pytest -x tests/test_a.py",
        "pytest -m slow",
        "pytest -w x",
        "cargo test -p x",
        "cargo llvm-cov -p x --lcov --output-path lcov.info",
        "npm test -w x",
        "./gradlew test -p x",
        "go test -C sub ./...",
        "cargo test --package foo",
        "npm test --workspace=foo",
        "mvn test -pl core",
    ],
)
def test_partial_runs_are_not_full(command) -> None:
    assert not hook.is_full_test_run(command)


@pytest.mark.parametrize(
    "command",
    ["pytestify", "git commit -m pytest", "npm install", "echo pytest", "go build", None, 3],
)
def test_other_commands_are_not_test_runs(command) -> None:
    assert not hook.is_test_command(command)


def test_default_reports_match_discovery() -> None:
    from repowise.core.analysis.health.coverage.discovery import DEFAULT_DISCOVERY_GLOBS

    literal = tuple(g for g in DEFAULT_DISCOVERY_GLOBS if not any(ch in g for ch in "*?["))
    assert literal == hook.DEFAULT_REPORTS


def test_a_fresh_report_is_reingested_once(repo, spawned) -> None:
    _report(repo)

    note = hook.coverage_reingest_notice({"command": "pytest -q"}, str(repo))

    assert note == (
        "[repowise] Re-ingesting coverage from coverage/lcov.info in the background "
        "(log: .repowise/.coverage.log); get_change_risk reads it once the ingest finishes."
    )
    ((root, reports),) = spawned
    assert root == repo.resolve()
    assert reports == [repo.resolve() / "coverage" / "lcov.info"]
    # The queued marker stops a second run from spawning over the first.
    assert hook.coverage_reingest_notice({"command": "pytest -q"}, str(repo)) is None
    assert len(spawned) == 1
    # A newer report than the queued one spawns again.
    _report(repo, after=120)
    assert hook.coverage_reingest_notice({"command": "pytest -q"}, str(repo)) is not None
    assert len(spawned) == 2


def test_one_fresh_report_reingests_every_watched_report(repo, spawned) -> None:
    """The ingest replaces stored coverage, so an older shard must ride along."""
    _report(repo, "coverage.xml", after=-60)
    _report(repo)
    _report(repo, ".coverage")  # a coverage.py database: never on its own account
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is not None
    assert spawned[0][1] == [
        repo.resolve() / "coverage" / "lcov.info",
        repo.resolve() / "coverage.xml",
    ]


def test_nothing_fresh_spawns_nothing(repo, spawned) -> None:
    _report(repo, after=-60)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None


def test_an_absolute_glob_skips_pruned_dirs(repo, spawned) -> None:
    _config(repo, f"coverage:\n  paths: ['{(repo / 'out').as_posix()}/*/lcov.info']\n")
    _report(repo, "out/web/lcov.info")
    _report(repo, "out/node_modules/lcov.info")
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is not None
    assert [Path(p).resolve() for p in spawned[0][1]] == [
        (repo / "out" / "web" / "lcov.info").resolve()
    ]


@pytest.mark.parametrize("var", ["REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"])
def test_a_configured_database_keeps_the_hook_quiet(repo, spawned, monkeypatch, var) -> None:
    _report(repo)
    monkeypatch.setenv(var, "postgresql://x/y")
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None


def test_a_fresh_coverage_database_alone_does_nothing(repo, spawned) -> None:
    _config(repo, "coverage:\n  paths: [.coverage]\n")
    _report(repo, ".coverage")
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None
    assert spawned == []


def test_a_stale_queued_marker_does_not_block(repo, spawned) -> None:
    _report(repo)
    (repo / ".repowise" / hook.QUEUED_FILENAME).write_text(
        f'{{"queued_at": {time.time() - hook.QUEUED_STALE_AFTER_SECONDS - 1}, '
        f'"report_mtime": {_INGESTED_EPOCH + 60}}}',
        encoding="utf-8",
    )
    assert hook.coverage_reingest_notice({"command": "go test ./..."}, str(repo)) is not None


def test_silent_for_partial_runs_other_commands_and_unindexed_repos(
    repo, tmp_path_factory, spawned
) -> None:
    _report(repo)
    assert hook.coverage_reingest_notice({"command": "git status"}, str(repo)) is None
    assert hook.coverage_reingest_notice({"command": "pytest -k login"}, str(repo)) is None
    bare = tmp_path_factory.mktemp("bare")
    (bare / ".repowise").mkdir()
    _report(bare)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(bare)) is None
    assert spawned == []


def test_an_upgraded_index_reads_its_ingests_from_the_coverage_rows(tmp_path, spawned) -> None:
    # The ingest history exists but is empty; earlier ingests live on the rows.
    _db(tmp_path)
    con = sqlite3.connect(tmp_path / ".repowise" / "wiki.db")
    con.execute("CREATE TABLE coverage_files (ingested_at DATETIME)")
    con.execute("INSERT INTO coverage_files VALUES (?)", (_INGESTED_AT,))
    con.commit()
    con.close()
    _config(tmp_path, "")
    _report(tmp_path)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(tmp_path)) is not None
    assert len(spawned) == 1


def test_a_repo_that_never_ingested_coverage_is_left_alone(tmp_path, spawned) -> None:
    _db(tmp_path)
    _config(tmp_path, "")
    _report(tmp_path)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(tmp_path)) is None
    assert spawned == []


def test_off_by_default_and_the_env_override(repo, spawned, monkeypatch) -> None:
    _report(repo)
    for block in ("", "hooks:\n  coverage_reingest: false\n", "coverage_reingest: true\n"):
        # Absent, false, or not under ``hooks:``: all off.
        (repo / ".repowise" / "config.yaml").write_text(block, encoding="utf-8")
        assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None
    assert spawned == []
    monkeypatch.setenv("REPOWISE_HOOK_COVERAGE_REINGEST", "1")  # one session
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is not None
    monkeypatch.setenv("REPOWISE_HOOK_COVERAGE_REINGEST", "0")
    _report(repo, after=120)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None


def test_configured_paths_and_discovery_settings_are_respected(repo, spawned) -> None:
    _config(repo, "coverage:\n  paths: [out/cov.info]\n")
    _report(repo)  # the default location, not configured
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None
    _report(repo, "out/cov.info")
    assert "out/cov.info" in hook.coverage_reingest_notice({"command": "pytest"}, str(repo))
    assert spawned[0][1] == [repo.resolve() / "out" / "cov.info"]

    (repo / ".repowise" / hook.QUEUED_FILENAME).unlink()
    for block in ("coverage:\n  auto_discover: false\n", "coverage:\n  artifacts: ['x/*.info']\n"):
        _config(repo, block)
        assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None
    assert len(spawned) == 1


def test_never_raises(repo, monkeypatch) -> None:
    _report(repo)

    def _boom(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(hook, "spawn_coverage_add", _boom)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None


def test_the_codex_shell_dispatch_respects_the_flag(repo, spawned) -> None:
    _report(repo)
    args = ("Bash", {"command": "pytest"}, {"stdout": "1 passed", "exit_code": 0}, str(repo))
    _config(repo, "hooks:\n  coverage_reingest: false\n")
    assert "Re-ingesting" not in (_handle_post_tool_use(*args, client="codex").context or "")
    assert spawned == []
    _config(repo, "")
    result = _handle_post_tool_use(*args, client="codex")
    assert "Re-ingesting coverage" in (result.context or "")


def test_spawn_runs_coverage_add_detached_with_a_fresh_log(tmp_path, monkeypatch) -> None:
    import subprocess
    import sys

    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / hook.LOG_FILENAME).write_text("old run\n", encoding="utf-8")
    seen: dict = {}

    def _popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    hook.spawn_coverage_add(tmp_path, [tmp_path / "lcov.info"])

    assert seen["argv"] == [
        sys.executable, "-m", "repowise.cli.main", "coverage", "add",
        "--path", str(tmp_path), str(tmp_path / "lcov.info"),
    ]
    assert seen["stdin"] is subprocess.DEVNULL
    assert "creationflags" in seen or seen.get("start_new_session") is True
    assert (tmp_path / ".repowise" / hook.LOG_FILENAME).read_text(encoding="utf-8") == ""


def test_a_mapped_config_entry_is_reingested_with_its_prefix(repo, spawned) -> None:
    _config(
        repo,
        "coverage:\n  paths:\n    - {path: 'web/*.info', path_prefix: web}\n    - out/cov.info\n",
    )
    _report(repo, "web/lcov.info")
    _report(repo, "out/cov.info")
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is not None
    assert spawned[0][1] == [
        f"{repo.resolve() / 'web' / 'lcov.info'}=web",
        repo.resolve() / "out" / "cov.info",
    ]


def test_a_report_only_a_recursive_glob_finds_is_not_watched(repo, spawned) -> None:
    _config(repo, "coverage:\n  paths: ['**/lcov.info']\n")
    _report(repo)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None


def _run_hook(payload: dict, monkeypatch, tmp_dir: Path, **kwargs) -> str:
    """The real stdin/stdout entry point; a private tempdir isolates the emission dedup marker."""
    out = io.StringIO()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_dir))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", out)
    _run_augment(**kwargs)
    return out.getvalue()


def _shell_payload(repo, event: str, command: str, **extra) -> dict:
    return {
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(repo),
        "session_id": "s1",
        **extra,
    }


def test_a_failing_test_run_is_reingested_from_the_failure_event(
    repo, spawned, monkeypatch, tmp_path_factory
) -> None:
    """Claude Code delivers a non-zero exit as PostToolUseFailure, with the rewritten command."""
    _report(repo)
    payload = _shell_payload(
        repo,
        "PostToolUseFailure",
        "repowise distill --source hook 'pytest -q'",
        error="Exit code 1\n1 failed, 3 passed",
        is_interrupt=False,
    )
    out = _run_hook(payload, monkeypatch, tmp_path_factory.mktemp("tmp"), coverage_only=True)
    assert "Re-ingesting coverage" in out
    assert len(spawned) == 1


def test_an_interrupted_test_run_is_left_alone(
    repo, spawned, monkeypatch, tmp_path_factory
) -> None:
    _report(repo)
    payload = _shell_payload(
        repo, "PostToolUseFailure", "pytest", error="Interrupted", is_interrupt=True
    )
    out = _run_hook(payload, monkeypatch, tmp_path_factory.mktemp("tmp"), coverage_only=True)
    assert out == ""
    assert spawned == []


def test_coverage_only_runs_nothing_else(repo, spawned, monkeypatch, tmp_path_factory) -> None:
    """The repo-local entry must not revive the shell notices the shared matcher dropped."""
    _report(repo)
    payload = _shell_payload(repo, "PostToolUse", "git commit -m x", tool_response={})
    out = _run_hook(payload, monkeypatch, tmp_path_factory.mktemp("tmp"), coverage_only=True)
    assert out == ""


def test_the_full_claude_handler_leaves_coverage_to_its_own_entry(repo, spawned) -> None:
    """Otherwise a capture-prompt shell entry and the coverage entry would both spawn."""
    _report(repo)
    result = _handle_post_tool_use("Bash", {"command": "pytest"}, {"stdout": ""}, str(repo))
    assert "Re-ingesting" not in (result.context or "")
    assert spawned == []


@pytest.mark.parametrize("nt", [True, False])
def test_spawn_breaks_away_from_the_job_and_retries_inside_it(monkeypatch, nt) -> None:
    import subprocess

    from repowise.cli import spawn

    calls: list[dict] = []

    def _popen(argv, **kwargs):
        calls.append(kwargs)
        if kwargs.get("creationflags", 0) & spawn._CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(spawn.os, "name", "nt" if nt else "posix")
    monkeypatch.setattr(subprocess, "Popen", _popen)
    spawn.spawn_detached(["x"], ".")

    if nt:
        assert [c["creationflags"] for c in calls] == [
            spawn._WINDOWS_FLAGS | spawn._CREATE_BREAKAWAY_FROM_JOB,
            spawn._WINDOWS_FLAGS,
        ]
    else:
        assert len(calls) == 1 and calls[0]["start_new_session"] is True


# --- the repo-local Claude Code entries --------------------------------------


@pytest.fixture
def claude_home(tmp_path_factory, monkeypatch) -> Path:
    """A user settings.json carrying the repowise augment hooks."""
    from repowise.cli.editor_integrations import claude_config

    path = tmp_path_factory.mktemp("home") / "settings.json"
    entry = {
        "matcher": claude_config._AUGMENT_MATCHER,
        "hooks": [{"type": "command", "command": claude_config._AUGMENT_HOOK_COMMAND}],
    }
    path.write_text(json.dumps({"hooks": {"PostToolUse": [entry]}}), encoding="utf-8")
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: path)
    return path


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: str) -> None:
        self.lines.append(text)


def _local_hooks(repo: Path) -> dict:
    path = repo / ".claude" / "settings.local.json"
    return json.loads(path.read_text(encoding="utf-8")).get("hooks", {}) if path.exists() else {}


def test_sync_adds_the_coverage_only_entry_under_both_events(repo, claude_home) -> None:
    console = _Console()
    hook.sync_repo_hook(repo, console)

    hooks = _local_hooks(repo)
    for event in ("PostToolUse", "PostToolUseFailure"):
        (entry,) = hooks[event]
        assert entry["matcher"] == "Bash|PowerShell"
        assert "repowise-augment --coverage-only" in entry["hooks"][0]["command"]
    assert "statusMessage" not in hooks["PostToolUse"][0]["hooks"][0]  # runs silently
    (line,) = console.lines
    assert "this repository only" in line and "settings.local.json" in line
    hook.sync_repo_hook(repo, console)  # idempotent, and silent when nothing changes
    assert len(console.lines) == 1


def test_default_config_writes_nothing(repo, claude_home) -> None:
    (repo / ".repowise" / "config.yaml").unlink()
    console = _Console()
    hook.sync_repo_hook(repo, console)
    assert _local_hooks(repo) == {}
    assert console.lines == []


def test_turning_the_flag_off_again_removes_the_entries(repo, claude_home) -> None:
    hook.sync_repo_hook(repo, _Console())
    (repo / ".repowise" / "config.yaml").unlink()  # absent reads as off
    hook.sync_repo_hook(repo, _Console())
    assert _local_hooks(repo) == {}


def test_the_opt_out_removes_only_our_entries(repo, claude_home) -> None:
    local = repo / ".claude" / "settings.local.json"
    local.parent.mkdir()
    local.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
    hook.sync_repo_hook(repo, _Console())
    assert _local_hooks(repo)

    _config(repo, "hooks:\n  coverage_reingest: false\n")
    console = _Console()
    hook.sync_repo_hook(repo, console)

    assert json.loads(local.read_text(encoding="utf-8")) == {
        "permissions": {"allow": ["Bash(ls)"]}
    }
    assert "Removed" in console.lines[0]


def test_a_file_the_opt_out_empties_is_removed(repo, claude_home) -> None:
    hook.sync_repo_hook(repo, _Console())
    _config(repo, "hooks:\n  coverage_reingest: false\n")
    hook.sync_repo_hook(repo, _Console())
    assert not (repo / ".claude" / "settings.local.json").exists()


def test_sync_needs_a_stored_ingest_and_the_augment_install(
    repo, tmp_path_factory, claude_home
) -> None:
    never = tmp_path_factory.mktemp("never")
    _db(never)
    hook.sync_repo_hook(never, _Console())
    assert _local_hooks(never) == {}

    claude_home.write_text("{}", encoding="utf-8")
    hook.sync_repo_hook(repo, _Console())
    assert _local_hooks(repo) == {}


def test_uninstall_sweeps_the_repo_local_entries(repo, claude_home) -> None:
    from repowise.cli.agent_targets.targets.claude_code import ClaudeCodeTarget
    from repowise.cli.agent_targets.types import Scope

    hook.sync_repo_hook(repo, _Console())
    local = str(repo / ".claude" / "settings.local.json")
    assert local in ClaudeCodeTarget().describe_paths(Scope.PROJECT, repo_path=repo)

    ClaudeCodeTarget().uninstall(Scope.PROJECT, repo_path=repo)

    assert _local_hooks(repo) == {}


def test_only_our_shell_entry_is_owned(repo, claude_home) -> None:
    """A repowise ``--coverage-only`` command under another matcher is not ours to remove."""
    from repowise.cli.agent_targets.targets.claude_code import ClaudeCodeTarget
    from repowise.cli.agent_targets.types import Scope
    from repowise.cli.editor_integrations import claude_config

    foreign = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "repowise-augment --coverage-only --mine"}],
    }
    local = repo / ".claude" / "settings.local.json"
    local.parent.mkdir()
    local.write_text(json.dumps({"hooks": {"PostToolUse": [foreign]}}), encoding="utf-8")

    hook.sync_repo_hook(repo, _Console())
    assert len(_local_hooks(repo)["PostToolUse"]) == 2
    claude_config.set_repo_coverage_hook(repo, False)
    assert _local_hooks(repo) == {"PostToolUse": [foreign]}

    hook.sync_repo_hook(repo, _Console())
    ClaudeCodeTarget().uninstall(Scope.PROJECT, repo_path=repo)
    assert _local_hooks(repo) == {"PostToolUse": [foreign]}

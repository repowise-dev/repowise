"""PostToolUse Bash: a full test run with a fresh coverage report re-ingests it in the background."""

from __future__ import annotations

import os
import sqlite3
import time

import pytest

from repowise.cli.commands.augment_cmd import coverage_reingest as hook
from repowise.cli.commands.augment_cmd.command import _handle_post_tool_use

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


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An indexed repo whose last coverage ingest is ``_INGESTED_AT``."""
    monkeypatch.delenv("REPOWISE_HOOK_COVERAGE_REINGEST", raising=False)
    _db(tmp_path, _INGESTED_AT)
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
    (repo / ".repowise" / "config.yaml").write_text(text, encoding="utf-8")


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


def test_only_the_fresh_reports_are_passed(repo, spawned) -> None:
    _report(repo, "coverage.xml", after=-60)  # older than the ingest: left alone
    _report(repo)
    _report(repo, ".coverage")  # a coverage.py database: never on its own account
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is not None
    assert spawned[0][1] == [repo.resolve() / "coverage" / "lcov.info"]


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
    _report(tmp_path)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(tmp_path)) is not None
    assert len(spawned) == 1


def test_a_repo_that_never_ingested_coverage_is_left_alone(tmp_path, spawned) -> None:
    _db(tmp_path)
    _report(tmp_path)
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(tmp_path)) is None
    assert spawned == []


def test_the_opt_out_and_its_env_override(repo, spawned, monkeypatch) -> None:
    _report(repo)
    _config(repo, "hooks:\n  coverage_reingest: false\n")
    assert hook.coverage_reingest_notice({"command": "pytest"}, str(repo)) is None
    monkeypatch.setenv("REPOWISE_HOOK_COVERAGE_REINGEST", "1")
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


def test_the_bash_dispatch_carries_the_note(repo, spawned) -> None:
    _report(repo)
    result = _handle_post_tool_use(
        "Bash", {"command": "pytest"}, {"stdout": "1 passed", "exit_code": 0}, str(repo)
    )
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

"""Tests for the ``coverage add`` command boundary."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands import coverage_cmd
from repowise.cli.main import cli
from repowise.core.persistence.crud.analysis.coverage_map import TestMapWrite


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_coverage_add_configures_logging_before_repo_resolution(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, bool | None]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_resolve_coverage_repo(_path: str | None) -> None:
        events.append(("repo", None))
        raise click.ClickException("stop after repo resolution")

    monkeypatch.setattr(coverage_cmd, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", fake_resolve_coverage_repo)

    result = CliRunner().invoke(cli, ["coverage", "add", *extra_args])

    assert result.exit_code == 1
    assert "stop after repo resolution" in result.output
    assert events == [("logging", expected_verbose), ("repo", None)]


def test_coverage_add_help_lists_verbose() -> None:
    result = CliRunner().invoke(cli, ["coverage", "add", "--help"])

    assert result.exit_code == 0
    assert "--verbose" in result.output


def _no_discovered_reports(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Point the command at an empty repo so auto-discovery finds nothing."""
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", lambda _p: tmp_path)
    monkeypatch.setattr(coverage_cmd, "ensure_repowise_dir", lambda _p: None)
    monkeypatch.setattr(coverage_cmd, "_discover_context_reports", lambda _p: [])


def test_coverage_add_exits_non_zero_when_it_discovers_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    A refresh is scripted as `coverage add ... || exit 1`, so a run that stored
    nothing has to be distinguishable from a complete one by its exit status.
    """
    _no_discovered_reports(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli, ["coverage", "add"])

    assert result.exit_code == 1
    assert "No coverage report found" in result.output


@pytest.mark.parametrize(("stored", "expected_exit"), [(True, 0), (False, 1)])
def test_coverage_add_exit_status_follows_whether_anything_was_stored(
    monkeypatch: pytest.MonkeyPatch, tmp_path, stored: bool, expected_exit: int
) -> None:
    """
    Every no-op branch inside the ingest returns False -- no index, no indexed
    files, nothing mapped, or --strict with unmapped report files. This pins
    the wiring that turns that answer into the process exit status.
    """
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", lambda _p: tmp_path)
    monkeypatch.setattr(coverage_cmd, "ensure_repowise_dir", lambda _p: None)

    def fake_run_async(coro):
        coro.close()  # never awaited; the DB is not part of this test
        return stored

    monkeypatch.setattr(coverage_cmd, "run_async", fake_run_async)

    report = tmp_path / "lcov.info"
    report.write_text("TN:", encoding="utf-8")

    result = CliRunner().invoke(cli, ["coverage", "add", str(report)])

    assert result.exit_code == expected_exit


def _coverage_add_source() -> str:
    """Source of the `coverage add` callback (Click wraps it in a Command)."""
    import inspect

    return inspect.getsource(coverage_cmd.coverage_add.callback)


def test_strict_counts_unmapped_before_the_resolved_files_branch() -> None:
    """Total mapping loss on the aggregate leg must still be able to trip --strict.

    The count used to be read inside `if resolved.files:`, so the one run where
    every report file failed to map was also the run that saw zero unmapped
    files. With a per-test map present, `map_records` is non-empty, the
    "nothing mapped" branch is skipped, and a --strict run exited 0 having
    stored no per-file coverage at all.

    This is a structural guard, not a behavioural one: the branch lives in the
    async closure that needs a DB session, so this pins the ordering that makes
    the branch reachable rather than the branch itself.
    """
    source = _coverage_add_source()

    assert source.index("unmapped = len(skipped)") < source.index("if resolved.files:"), (
        "the unmapped count must be computed before the resolved.files branch"
    )


def test_strict_failure_is_reported_before_the_success_guidance() -> None:
    """A failing --strict run must not first advise running `repowise health`."""
    source = _coverage_add_source()

    assert source.index("if strict and unmapped:") < source.index(
        "to fold coverage into the defect "
    )


def test_coverage_add_help_documents_strict() -> None:
    result = CliRunner().invoke(cli, ["coverage", "add", "--help"])

    assert result.exit_code == 0
    assert "--strict" in result.output


def test_coverage_add_stamps_live_head_not_stored_column(tmp_path, monkeypatch) -> None:
    """Regression for #1747: coverage describes the working tree, so it must be
    stamped with the live HEAD, not the stored ``head_commit`` column.

    The stored column names the last *indexed* commit. Commit, run tests, then
    ``coverage add`` without an intervening ``repowise update``: the coverage
    describes the working tree, the column names the older indexed commit, and
    they differ. This test fails on main, which stamps the stale column.
    """
    import git as gitpython

    from repowise.cli.commands import coverage_cmd

    # A real repo with one commit; the stored column will claim an older sha.
    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x = 1\n")
    repo.index.add(["src/foo.py"])
    repo.index.commit("feat: add foo")
    live_head = repo.head.commit.hexsha
    repo.close()

    lcov = tmp_path / "coverage" / "lcov.info"
    lcov.parent.mkdir()
    lcov.write_text("TN:\nSF:src/foo.py\nDA:1,2\nLF:1\nLH:1\nend_of_record\n")

    class _FakeRepoRow:
        id = "repo-1"
        head_commit = "0" * 40  # stale: the indexed commit, not live HEAD

    recorded: dict = {}

    async def _fake_save_coverage_files(session, repository_id, files, **kwargs):
        recorded["sha"] = kwargs.get("ingested_commit_sha")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _fake_get_repository_by_path(session, local_path):
        return _FakeRepoRow()

    monkeypatch.setattr(
        "repowise.core.persistence.crud.get_repository_by_path",
        _fake_get_repository_by_path,
    )
    monkeypatch.setattr(
        "repowise.core.persistence.crud.save_coverage_files", _fake_save_coverage_files
    )
    async def _fake_repo_file_keys(session, repo_id):
        return {"src/foo.py"}

    monkeypatch.setattr(coverage_cmd, "_repo_file_keys", _fake_repo_file_keys)
    monkeypatch.setattr(coverage_cmd, "get_db_url_for_repo", lambda path: "sqlite:///:memory:")

    async def _no_reconcile(_url):
        return None

    monkeypatch.setattr(coverage_cmd, "reconcile_schema_best_effort", _no_reconcile)
    monkeypatch.setattr("repowise.core.persistence.create_engine", lambda url: object())
    monkeypatch.setattr("repowise.core.persistence.create_session_factory", lambda engine: object())
    monkeypatch.setattr("repowise.core.persistence.get_session", lambda sf: _FakeSession())

    result = CliRunner().invoke(
        cli, ["coverage", "add", str(lcov), "--path", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert recorded["sha"] == live_head
    assert recorded["sha"] != _FakeRepoRow.head_commit


def test_coverage_add_stamps_a_leftover_report_at_the_commit_it_measured(
    tmp_path, monkeypatch
) -> None:
    """Regression for the P1 on #3935: the stamp follows the reports that gave
    rows, not every file discovery turned up.

    Two commits a week apart, a report from the first afternoon, and an empty
    leftover from months ago sitting in the same directory. The aggregate is
    measured at the first commit, so that is what gets stamped — live HEAD
    would strand the drift check on a tree the report never saw. The leftover
    is skipped by the parser, so its date must not enter the arithmetic: had it,
    no commit would predate it and the writer would fall back to live HEAD.
    """
    import os
    from datetime import UTC, datetime

    import git as gitpython

    from repowise.cli.commands import coverage_cmd

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x = 1\n")
    repo.index.add(["src/foo.py"])
    july = int(datetime(2026, 7, 3, 12, 0, tzinfo=UTC).timestamp())
    measured_at_commit = repo.index.commit(
        "feat: add foo",
        commit_date=datetime.fromtimestamp(july, UTC).strftime("%Y-%m-%d %H:%M:%S +0000"),
        author_date=datetime.fromtimestamp(july, UTC).strftime("%Y-%m-%d %H:%M:%S +0000"),
    ).hexsha

    lcov = tmp_path / "coverage" / "lcov.info"
    lcov.parent.mkdir()
    lcov.write_text("TN:\nSF:src/foo.py\nDA:1,2\nLF:1\nLH:1\nend_of_record\n")
    # Written by the suite an hour after that commit.
    run = july + 3600
    os.utime(lcov, (run, run))
    # A leftover nobody deleted, months older and yielding nothing.
    stale = tmp_path / "coverage" / "stale.info"
    stale.write_text("")
    old = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())
    os.utime(stale, (old, old))

    (tmp_path / "src" / "bar.py").write_text("y = 2\n")
    repo.index.add(["src/bar.py"])
    live_head = repo.index.commit("feat: add bar").hexsha
    repo.close()

    class _FakeRepoRow:
        id = "repo-1"
        head_commit = None

    recorded: dict = {}

    async def _fake_save_coverage_files(session, repository_id, files, **kwargs):
        recorded["sha"] = kwargs.get("ingested_commit_sha")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _fake_get_repository_by_path(session, local_path):
        return _FakeRepoRow()

    monkeypatch.setattr(
        "repowise.core.persistence.crud.get_repository_by_path",
        _fake_get_repository_by_path,
    )
    monkeypatch.setattr(
        "repowise.core.persistence.crud.save_coverage_files", _fake_save_coverage_files
    )

    async def _fake_repo_file_keys(session, repo_id):
        return {"src/foo.py", "src/bar.py"}

    monkeypatch.setattr(coverage_cmd, "_repo_file_keys", _fake_repo_file_keys)
    monkeypatch.setattr(coverage_cmd, "get_db_url_for_repo", lambda path: "sqlite:///:memory:")

    async def _no_reconcile(_url):
        return None

    monkeypatch.setattr(coverage_cmd, "reconcile_schema_best_effort", _no_reconcile)
    monkeypatch.setattr("repowise.core.persistence.create_engine", lambda url: object())
    monkeypatch.setattr(
        "repowise.core.persistence.create_session_factory", lambda engine: object()
    )
    monkeypatch.setattr("repowise.core.persistence.get_session", lambda sf: _FakeSession())

    result = CliRunner().invoke(
        cli, ["coverage", "add", str(lcov), str(stale), "--path", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert recorded["sha"] == measured_at_commit
    assert recorded["sha"] != live_head


def _fake_ingest(monkeypatch: pytest.MonkeyPatch, tmp_path, keys: set[str]) -> list:
    """Run `coverage add` against *tmp_path* with *keys* indexed; returns the stored rows."""

    class _Row:
        id = "repo-1"
        head_commit = None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    stored: list = []

    async def _save(session, repository_id, files, **kwargs):
        stored.extend(files)

    async def _repo(session, local_path):
        return _Row()

    async def _keys(session, repo_id):
        return keys

    async def _noop(_url):
        return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", lambda _p: tmp_path)
    monkeypatch.setattr(coverage_cmd, "_repo_file_keys", _keys)
    monkeypatch.setattr(coverage_cmd, "get_db_url_for_repo", lambda path: "sqlite:///:memory:")
    monkeypatch.setattr(coverage_cmd, "reconcile_schema_best_effort", _noop)
    monkeypatch.setattr("repowise.core.persistence.crud.get_repository_by_path", _repo)
    monkeypatch.setattr("repowise.core.persistence.crud.save_coverage_files", _save)
    monkeypatch.setattr("repowise.core.persistence.create_engine", lambda url: object())
    monkeypatch.setattr("repowise.core.persistence.create_session_factory", lambda e: object())
    monkeypatch.setattr("repowise.core.persistence.get_session", lambda sf: _Session())
    return stored


def _lcov(path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"SF:{source}\nDA:1,1\nend_of_record\n", encoding="utf-8")


def test_coverage_add_expands_a_glob(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    stored = _fake_ingest(monkeypatch, tmp_path, {"a.py", "b.py"})
    _lcov(tmp_path / "artifacts" / "one" / "lcov.info", "a.py")
    _lcov(tmp_path / "artifacts" / "two" / "lcov.info", "b.py")

    result = CliRunner().invoke(cli, ["coverage", "add", "artifacts/**/lcov.info"])

    assert result.exit_code == 0, result.output
    assert sorted(f.file_path for f in stored) == ["a.py", "b.py"]


def test_coverage_add_applies_a_prefix_to_every_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    stored = _fake_ingest(monkeypatch, tmp_path, {"web/src/a.ts", "src/a.ts"})
    _lcov(tmp_path / "web" / "coverage" / "lcov.info", "src/a.ts")

    result = CliRunner().invoke(cli, ["coverage", "add", "web/*/lcov.info=web"])

    assert result.exit_code == 0, result.output
    assert [f.file_path for f in stored] == ["web/src/a.ts"]


def test_coverage_add_rejects_an_argument_matching_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _fake_ingest(monkeypatch, tmp_path, {"a.py"})

    result = CliRunner().invoke(cli, ["coverage", "add", "nope/**/lcov.info"])

    assert result.exit_code == 2
    assert "no coverage report matches nope/**/lcov.info" in result.output


def test_coverage_add_adds_the_repo_hook_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json

    from repowise.cli.commands.augment_cmd import coverage_reingest
    from repowise.cli.editor_integrations import claude_config

    _fake_ingest(monkeypatch, tmp_path, {"a.py"})
    _lcov(tmp_path / "lcov.info", "a.py")
    (tmp_path / ".repowise").mkdir(exist_ok=True)
    (tmp_path / ".repowise" / "wiki.db").write_bytes(b"")
    user = tmp_path / "user-settings.json"
    entry = {"matcher": "Read", "hooks": [{"command": claude_config._AUGMENT_HOOK_COMMAND}]}
    user.write_text(json.dumps({"hooks": {"PostToolUse": [entry]}}), encoding="utf-8")
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: user)
    monkeypatch.setattr(coverage_reingest, "last_ingest_time", lambda _db: 1.0)
    monkeypatch.delenv("REPOWISE_HOOK_COVERAGE_REINGEST", raising=False)
    config = tmp_path / ".repowise" / "config.yaml"

    result = CliRunner().invoke(cli, ["coverage", "add", "lcov.info"])  # off by default
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude" / "settings.local.json").exists()

    config.write_text("hooks:\n  coverage_reingest: true\n", encoding="utf-8")
    result = CliRunner().invoke(cli, ["coverage", "add", "lcov.info"])

    assert result.exit_code == 0, result.output
    assert "this repository only" in result.output
    assert (tmp_path / ".claude" / "settings.local.json").is_file()

    (tmp_path / ".repowise" / "config.yaml").write_text(
        "hooks:\n  coverage_reingest: false\n", encoding="utf-8"
    )
    result = CliRunner().invoke(cli, ["coverage", "add", "lcov.info"])
    assert "Removed the coverage re-ingest hook" in result.output
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_init_and_update_keep_the_repo_hook_in_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Both store coverage, so both sync the hook: update on every outcome, init after setup."""
    import inspect

    from repowise.cli.commands.augment_cmd import coverage_reingest
    from repowise.cli.commands.init_cmd import _finish as init_finish
    from repowise.cli.commands.update_cmd import command as update_command

    calls: list = []
    monkeypatch.setattr(coverage_reingest, "sync_repo_hook", lambda repo, _c: calls.append(repo))
    monkeypatch.setattr(
        "repowise.cli.editor_setup.refresh_editor_project_files", lambda *a, **k: None
    )
    update_command._refresh_editor_stamp(tmp_path, None)
    assert calls == [tmp_path]
    assert "sync_repo_hook(repo_path, console)" in inspect.getsource(init_finish._write_editor_files)


def _coverage_data(path, context: str | None, source) -> None:
    """A real coverage.py ``.coverage`` with lines 1-2 of *source* run under *context*."""
    coverage = pytest.importorskip("coverage")
    data = coverage.CoverageData(basename=str(path))
    if context:
        data.set_context(context)
    data.add_lines({str(source): [1, 2]})
    data.write()


def test_coverage_add_dot_coverage_builds_the_map_and_says_why_no_per_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    stored = _fake_ingest(monkeypatch, tmp_path, {"src/a.py"})
    mapped: list = []

    async def _save_map(session, repository_id, records, **kwargs):
        mapped.extend(records)
        return TestMapWrite(written=len(records))

    monkeypatch.setattr("repowise.core.persistence.crud.save_test_coverage", _save_map)
    _coverage_data(tmp_path / ".coverage", "tests/test_a.py::test_one|run", tmp_path / "src" / "a.py")

    result = CliRunner().invoke(cli, ["coverage", "add", ".coverage"])

    assert result.exit_code == 0, result.output
    # No executable-line set in the file, so no per-file row; the map is built.
    assert stored == []
    assert [(r.file_path, r.covered_lines) for r in mapped] == [("src/a.py", [1, 2])]
    assert "coverage lcov" in " ".join(result.output.split())


def test_coverage_add_dot_coverage_without_contexts_stores_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    stored = _fake_ingest(monkeypatch, tmp_path, {"src/a.py"})
    _coverage_data(tmp_path / ".coverage", None, tmp_path / "src" / "a.py")

    result = CliRunner().invoke(cli, ["coverage", "add", ".coverage"])

    output = " ".join(result.output.split())
    assert result.exit_code == 1, result.output
    assert stored == []
    assert "coverage lcov" in output
    assert "Nothing was stored." in output
    # The paths were never the problem, so no prefix advice.
    assert "strip_prefix" not in output


_MONOREPO_KEYS = {"web/src/a.py", "api/src/a.py", "web/src/b.py", "api/src/b.py", "web/src/c.py"}


def _package_report(tmp_path) -> None:
    """A web package's lcov, paths from the package root: two tie, one maps."""
    (tmp_path / "lcov.info").write_text(
        "".join(f"SF:src/{n}.py\nDA:1,1\nend_of_record\n" for n in "abc"), encoding="utf-8"
    )


def test_coverage_add_stores_nothing_from_a_partial_mapping_and_suggests_a_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    stored = _fake_ingest(monkeypatch, tmp_path, _MONOREPO_KEYS)
    _package_report(tmp_path)

    result = CliRunner().invoke(cli, ["coverage", "add", "lcov.info"])

    output = " ".join(result.output.split())
    assert result.exit_code == 1, result.output
    assert stored == []
    assert "nothing was stored" in output
    assert "set coverage.path_prefix: web in .repowise/config.yaml" in output
    assert "--allow-partial" in output


def test_coverage_add_allow_partial_stores_the_flagged_fragment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    stored = _fake_ingest(monkeypatch, tmp_path, _MONOREPO_KEYS)
    _package_report(tmp_path)

    result = CliRunner().invoke(cli, ["coverage", "add", "lcov.info", "--allow-partial"])

    assert result.exit_code == 0, result.output
    assert [f.file_path for f in stored] == ["web/src/c.py"]
    assert "flagged partial" in " ".join(result.output.split())


def test_coverage_add_withholds_a_partial_per_test_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _fake_ingest(monkeypatch, tmp_path, {"src/a.py"})
    mapped: list = []

    async def _save_map(session, repository_id, records, **kwargs):
        mapped.extend(records)
        return TestMapWrite(written=len(records))

    monkeypatch.setattr("repowise.core.persistence.crud.save_test_coverage", _save_map)
    coverage = pytest.importorskip("coverage")
    data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
    data.set_context("tests/test_a.py::test_one|run")
    data.add_lines({str(tmp_path / n): [1] for n in ("src/a.py", "gone/b.py", "gone/c.py")})
    data.write()

    result = CliRunner().invoke(cli, ["coverage", "add", ".coverage"])

    assert result.exit_code == 1, result.output
    assert mapped == []
    assert "More than half the per-test map did not map" in " ".join(result.output.split())


def test_coverage_add_maps_a_package_per_test_lcov_like_its_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    keys = {
        "packages/web/src/a.py",
        "packages/api/src/a.py",
        "packages/web/src/b.py",
        "packages/api/src/b.py",
    }
    stored = _fake_ingest(monkeypatch, tmp_path, keys)
    mapped: list = []

    async def _save_map(session, repository_id, records, **kwargs):
        mapped.extend(records)
        return TestMapWrite(written=len(records))

    monkeypatch.setattr("repowise.core.persistence.crud.save_test_coverage", _save_map)
    report = tmp_path / "packages" / "web" / "coverage" / "lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text(
        "".join(
            f"TN:tests/test_{n}.py::test_one|run\nSF:src/{n}.py\nDA:1,1\nend_of_record\n"
            for n in "ab"
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["coverage", "add", "packages/web/coverage/lcov.info"])

    assert result.exit_code == 0, result.output
    expected = ["packages/web/src/a.py", "packages/web/src/b.py"]
    assert sorted(f.file_path for f in stored) == expected
    assert sorted(r.file_path for r in mapped) == expected

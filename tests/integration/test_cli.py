"""Integration tests for the CLI — gate tests using MockProvider on sample_repo."""

from __future__ import annotations

import shutil

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def work_repo(tmp_path, sample_repo_path, monkeypatch):
    """Copy sample_repo into a temporary directory for isolation."""
    dest = tmp_path / "repo"
    shutil.copytree(sample_repo_path, dest)
    # Point the DB at the repo-local path so tests can assert on its existence
    db_path = dest / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    return dest


def _git(args, cwd):
    import subprocess

    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**env})


@pytest.fixture
def workspace_root(tmp_path, sample_repo_path, monkeypatch):
    """A directory holding two git-initialized copies of sample_repo.

    Each sub-repo is a real git repo (so the scanner detects >1 repo and routes
    into the workspace flow) and uses its own repo-local DB — so we must NOT set
    REPOWISE_DB_URL here.
    """
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = tmp_path / "ws"
    root.mkdir()
    for name in ("alpha", "beta"):
        dest = root / name
        shutil.copytree(sample_repo_path, dest)
        _git(["init"], dest)
        _git(["add", "-A"], dest)
        _git(["commit", "-m", "init"], dest)
    return root


@pytest.fixture
def git_work_repo(tmp_path, sample_repo_path, monkeypatch):
    """A git-backed copy of sample_repo (one commit), with a repo-local DB.

    ``repowise update`` diffs HEAD against the last synced commit, so the
    update path needs a real git repo with history.
    """
    dest = tmp_path / "gitrepo"
    shutil.copytree(sample_repo_path, dest)
    db_path = dest / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    _git(["init"], dest)
    _git(["add", "-A"], dest)
    _git(["commit", "-m", "init"], dest)
    return dest


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


class TestWorkspaceInitIndexOnly:
    def test_indexes_each_repo(self, runner, workspace_root):
        result = runner.invoke(
            cli,
            ["init", str(workspace_root), "--all", "--index-only"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "workspace init complete" in result.output
        # Each sub-repo got its own index + state, and a workspace config exists.
        for name in ("alpha", "beta"):
            assert (workspace_root / name / ".repowise" / "wiki.db").exists()
            assert (workspace_root / name / ".repowise" / "state.json").exists()
        assert (workspace_root / ".repowise-workspace.yaml").exists()


class TestInitDryRun:
    def test_exit_zero_shows_plan(self, runner, work_repo):
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--dry-run"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Generation Plan" in result.output
        assert "Dry run" in result.output
        # No DB should be created
        assert not (work_repo / ".repowise" / "wiki.db").exists()


class TestInitFullMock:
    def test_creates_db_and_state(self, runner, work_repo):
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (work_repo / ".repowise" / "wiki.db").exists()
        assert (work_repo / ".repowise" / "state.json").exists()
        assert "init complete" in result.output


class TestInitIndexOnly:
    def test_index_only_creates_db_and_state_no_pages(self, runner, work_repo):
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--index-only"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (work_repo / ".repowise" / "wiki.db").exists()
        assert (work_repo / ".repowise" / "state.json").exists()
        assert "index complete" in result.output

        import json

        state = json.loads((work_repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state.get("docs_enabled") is False
        # No pages generated in index-only mode.
        assert state.get("total_pages", 0) == 0


class TestInitDefaultDbLocation:
    def test_creates_repo_local_db_without_env_override(
        self,
        runner,
        tmp_path,
        sample_repo_path,
        monkeypatch,
    ):
        work_repo = tmp_path / "repo"
        shutil.copytree(sample_repo_path, work_repo)
        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
        monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (work_repo / ".repowise" / "wiki.db").exists()


class TestInitIdempotent:
    def test_running_init_twice(self, runner, work_repo):
        args = ["init", str(work_repo), "--provider", "mock", "--yes"]
        r1 = runner.invoke(cli, args, catch_exceptions=False)
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(cli, args, catch_exceptions=False)
        assert r2.exit_code == 0, r2.output


class TestStatusAfterInit:
    def test_shows_page_counts(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            cli,
            ["status", str(work_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Sync State" in result.output


class TestDoctorAfterInit:
    def test_passes_checks(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            cli,
            ["doctor", str(work_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "repowise Doctor" in result.output


class TestSearchFulltext:
    def test_returns_results_or_no_error(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            cli,
            ["search", "function", str(work_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output


class TestExportMarkdown:
    def test_creates_output_files(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        export_dir = work_repo / "export_out"
        result = runner.invoke(
            cli,
            ["export", str(work_repo), "--format", "markdown", "--output", str(export_dir)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # Should have created some .md files
        md_files = list(export_dir.glob("*.md"))
        assert len(md_files) > 0, f"No markdown files in {export_dir}"


class TestUpdateIndexOnly:
    def test_advances_sync_commit(self, runner, git_work_repo):
        import json

        # Index first (index-only — no LLM needed).
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output
        state0 = json.loads(
            (git_work_repo / ".repowise" / "state.json").read_text(encoding="utf-8")
        )
        base_commit = state0["last_sync_commit"]
        assert base_commit

        # Make a change and commit it so update has a diff to process.
        (git_work_repo / "new_module.py").write_text(
            "def added():\n    return 1\n", encoding="utf-8"
        )
        _git(["add", "-A"], git_work_repo)
        _git(["commit", "-m", "add module"], git_work_repo)

        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r1.exit_code == 0, r1.output
        assert "Index-only update complete" in r1.output

        state1 = json.loads(
            (git_work_repo / ".repowise" / "state.json").read_text(encoding="utf-8")
        )
        assert state1["last_sync_commit"] != base_commit


class TestUpdateFullMock:
    def test_regenerates_pages(self, runner, git_work_repo):
        import json

        r0 = runner.invoke(
            cli,
            ["init", str(git_work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert r0.exit_code == 0, r0.output

        (git_work_repo / "new_module.py").write_text(
            "def added():\n    return 1\n", encoding="utf-8"
        )
        _git(["add", "-A"], git_work_repo)
        _git(["commit", "-m", "add module"], git_work_repo)

        r1 = runner.invoke(
            cli,
            ["update", str(git_work_repo), "--provider", "mock", "--docs"],
            catch_exceptions=False,
        )
        assert r1.exit_code == 0, r1.output
        # State advanced and docs stayed enabled through a full update.
        state = json.loads((git_work_repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state.get("docs_enabled") is True


class TestUpdateNoChanges:
    def test_already_up_to_date(self, runner, git_work_repo):
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output
        # No new commits since init → update is a no-op.
        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r1.exit_code == 0, r1.output
        assert "Already up to date" in r1.output

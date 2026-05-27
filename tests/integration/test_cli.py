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


@pytest.fixture
def workspace_root(tmp_path, sample_repo_path, monkeypatch):
    """A directory holding two git-initialized copies of sample_repo.

    Each sub-repo is a real git repo (so the scanner detects >1 repo and routes
    into the workspace flow) and uses its own repo-local DB — so we must NOT set
    REPOWISE_DB_URL here.
    """
    import subprocess

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = tmp_path / "ws"
    root.mkdir()
    for name in ("alpha", "beta"):
        dest = root / name
        shutil.copytree(sample_repo_path, dest)
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e.x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e.x",
        }
        subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=dest,
            check=True,
            capture_output=True,
            env={**env},
        )
    return root


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

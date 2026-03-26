"""Integration tests for the CLI — gate tests using MockProvider on sample_repo."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def work_repo(tmp_path, sample_repo_path):
    """Copy sample_repo into a temporary directory for isolation."""
    dest = tmp_path / "repo"
    shutil.copytree(sample_repo_path, dest)
    return dest


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


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
        assert "Done!" in result.output


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

"""Unit tests for CLI commands using CliRunner."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from repowise.cli import __version__
from repowise.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Basic CLI tests
# ---------------------------------------------------------------------------


class TestCliBasics:
    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "repowise" in result.output
        assert __version__ in result.output

    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "repowise" in result.output

    def test_init_help(self, runner):
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "--provider" in result.output
        assert "--dry-run" in result.output
        assert "--skip-tests" in result.output

    def test_update_help(self, runner):
        result = runner.invoke(cli, ["update", "--help"])
        assert result.exit_code == 0
        assert "--since" in result.output

    def test_search_help(self, runner):
        result = runner.invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--mode" in result.output

    def test_export_help(self, runner):
        result = runner.invoke(cli, ["export", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.output

    def test_status_help(self, runner):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0

    def test_doctor_help(self, runner):
        result = runner.invoke(cli, ["doctor", "--help"])
        assert result.exit_code == 0

    def test_watch_help(self, runner):
        result = runner.invoke(cli, ["watch", "--help"])
        assert result.exit_code == 0
        assert "--debounce" in result.output


# ---------------------------------------------------------------------------
# Stub commands
# ---------------------------------------------------------------------------


class TestStubs:
    def test_serve_help(self, runner):
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.output

    def test_mcp_help(self, runner):
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "--transport" in result.output
        assert "stdio" in result.output


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_init_nonexistent_path(self, runner, tmp_path):
        bad_path = str(tmp_path / "nonexistent")
        result = runner.invoke(cli, ["init", bad_path])
        assert result.exit_code != 0

    def test_init_no_provider(self, runner, tmp_path, monkeypatch):
        """init with no provider configured should error."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)
        result = runner.invoke(cli, ["init", str(tmp_path)])
        assert result.exit_code != 0

    def test_status_no_repowise_dir(self, runner, tmp_path):
        result = runner.invoke(cli, ["status", str(tmp_path)])
        assert result.exit_code == 0
        assert "No .repowise/" in result.output

    def test_update_no_state(self, runner, tmp_path):
        """update without prior init should error."""
        (tmp_path / ".repowise").mkdir()
        result = runner.invoke(cli, ["update", str(tmp_path)])
        assert result.exit_code != 0

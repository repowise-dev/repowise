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
        assert "--reasoning" in result.output

    def test_update_help(self, runner):
        result = runner.invoke(cli, ["update", "--help"])
        assert result.exit_code == 0
        assert "--since" in result.output
        assert "--reasoning" in result.output

    def test_search_help(self, runner):
        result = runner.invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--mode" in result.output

    def test_reindex_help(self, runner):
        result = runner.invoke(cli, ["reindex", "--help"])
        assert result.exit_code == 0
        assert "--embedder" in result.output
        assert "mock" in result.output

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
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("LITELLM_API_KEY", raising=False)
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


class TestBuildFilteredChangedPaths:
    def test_excludes_matching_patterns(self):
        from unittest.mock import MagicMock

        from repowise.cli.commands.update_cmd import _build_filtered_changed_paths

        fds = [
            MagicMock(path="src/main.py"),
            MagicMock(path=".claude/config.yml"),
            MagicMock(path="tools/build.sh"),
        ]
        result = _build_filtered_changed_paths(fds, [".claude/", "tools/"])
        assert result == ["src/main.py"]

    def test_no_patterns_returns_all(self):
        from unittest.mock import MagicMock

        from repowise.cli.commands.update_cmd import _build_filtered_changed_paths

        fds = [MagicMock(path="src/main.py"), MagicMock(path=".claude/config.yml")]
        result = _build_filtered_changed_paths(fds, [])
        assert result == ["src/main.py", ".claude/config.yml"]


class TestGitMetadataToDict:
    def test_converts_orm_row_to_dict(self):
        from types import SimpleNamespace

        from repowise.cli.commands.update_cmd import _git_metadata_to_dict

        gm = SimpleNamespace(
            file_path="src/main.py",
            commit_count_total=42,
            commit_count_90d=10,
            commit_count_30d=3,
            first_commit_at=None,
            last_commit_at=None,
            primary_owner_name="alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.7,
            top_authors_json="[]",
            significant_commits_json="[]",
            co_change_partners_json="[]",
            commit_categories_json="{}",
            is_hotspot=True,
            is_stable=False,
            churn_percentile=0.9,
            age_days=100,
            commit_count_capped=False,
            lines_added_90d=120,
            lines_deleted_90d=30,
            avg_commit_size=15.0,
            recent_owner_name="alice",
            recent_owner_commit_pct=0.8,
            bus_factor=2,
            contributor_count=4,
            original_path=None,
            merge_commit_count_90d=1,
            temporal_hotspot_score=0.8,
            prior_defect_count=5,
            change_entropy=0.42,
            change_entropy_pct=0.6,
        )

        d = _git_metadata_to_dict(gm)
        assert d["file_path"] == "src/main.py"
        assert d["commit_count_total"] == 42
        assert d["is_hotspot"] is True
        assert d["bus_factor"] == 2
        # Columns added by the newer health biomarkers must flow through too.
        assert d["prior_defect_count"] == 5
        assert d["change_entropy"] == 0.42
        assert d["change_entropy_pct"] == 0.6


class TestRescoreFailureFingerprint:
    def test_failed_rescore_does_not_advance_fingerprint(self, tmp_path, monkeypatch):
        """A failed re-score must not persist the new fingerprint, so the next
        update retries instead of treating the config change as handled."""
        import json

        from repowise.cli.commands import update_cmd

        def _boom(coro):
            coro.close()  # avoid 'coroutine never awaited' warning
            raise RuntimeError("db down")

        monkeypatch.setattr(update_cmd, "run_async", _boom)
        (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")

        update_cmd._run_full_health_rescore(
            tmp_path, {}, [], {"last_sync_commit": "base"}, "head1", "NEWFP"
        )

        state_file = tmp_path / ".repowise" / "state.json"
        if state_file.exists():
            assert json.loads(state_file.read_text()).get("config_fingerprint") != "NEWFP"

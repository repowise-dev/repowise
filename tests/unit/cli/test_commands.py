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
        assert "xhigh" in result.output
        assert "--codex" in result.output
        assert "--agents" in result.output
        assert "--no-workspace" in result.output

    def test_update_help(self, runner):
        result = runner.invoke(cli, ["update", "--help"])
        assert result.exit_code == 0
        assert "--since" in result.output
        assert "--reasoning" in result.output
        assert "xhigh" in result.output

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
        assert "--verbose" in result.output


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
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
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


class TestInitNoWorkspaceFlag:
    """Tests for ``repowise init --no-workspace``."""

    def test_no_workspace_forces_single_repo(self, runner, tmp_path, monkeypatch):
        """--no-workspace skips the workspace branch even when scan returns >1 repo."""
        from unittest.mock import MagicMock, patch

        # Simulate a workspace with two repos detected.
        fake_repo = MagicMock()
        fake_repo.path = tmp_path
        fake_scan = MagicMock()
        fake_scan.repos = [fake_repo, MagicMock()]  # 2 repos -> would trigger workspace

        workspace_entered = []

        def fake_workspace_init(**kwargs):
            workspace_entered.append(True)

        # Patch scan_for_repos so we control what it returns, and patch
        # _workspace_init so we can detect if the workspace branch runs.
        with (
            patch(
                "repowise.core.workspace.scan_for_repos",
                return_value=fake_scan,
            ),
            patch(
                "repowise.cli.commands.init_cmd.command._workspace_init",
                side_effect=fake_workspace_init,
            ),
        ):
            # Without --no-workspace the workspace branch should be entered.
            result = runner.invoke(cli, ["init", str(tmp_path)])
            assert workspace_entered, "expected workspace branch without --no-workspace"

            workspace_entered.clear()

            # With --no-workspace the workspace branch must NOT be entered.
            result = runner.invoke(cli, ["init", "--no-workspace", str(tmp_path)])
            assert not workspace_entered, (
                f"--no-workspace should skip workspace branch, but it was entered. "
                f"exit_code={result.exit_code}, output={result.output}"
            )

    def test_no_workspace_in_help(self, runner):
        """--no-workspace flag is documented in init --help output."""
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "--no-workspace" in result.output


class TestInitYesFlag:
    """Tests that -y/--yes makes init fully non-interactive."""

    def test_yes_skips_offer_hook_install(self):
        """offer_hook_install returns immediately when yes=True, never calling click.confirm."""
        from pathlib import Path
        from unittest.mock import patch

        from repowise.cli.commands.init_cmd._interactive import offer_hook_install

        console_mock = type(
            "C", (), {"print": lambda self, *a, **kw: None, "line": lambda self: None}
        )()

        with patch(
            "click.confirm", side_effect=AssertionError("should not prompt")
        ) as mock_confirm:
            # With yes=True and a TTY-like stdin, confirm must never be called.
            offer_hook_install(console_mock, [Path("/tmp/repo")], yes=True)
            mock_confirm.assert_not_called()

    def test_yes_skips_offer_distill_rewrite_hook_when_flag_is_none(self):
        """offer_distill_rewrite_hook skips any prompt when yes=True and flag=None."""
        from pathlib import Path
        from unittest.mock import patch

        from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook

        console_mock = type(
            "C", (), {"print": lambda self, *a, **kw: None, "line": lambda self: None}
        )()

        with patch(
            "click.confirm", side_effect=AssertionError("should not prompt")
        ) as mock_confirm:
            offer_distill_rewrite_hook(console_mock, [Path("/tmp/repo")], flag=None, yes=True)
            mock_confirm.assert_not_called()

    def test_yes_skips_wiki_style_prompt(self, runner, tmp_path, monkeypatch):
        """With -y, the wiki-style interactive prompt is never shown."""
        from unittest.mock import MagicMock, patch

        # Single-repo scan (no workspace branch).
        fake_repo = MagicMock()
        fake_repo.path = tmp_path
        fake_scan = MagicMock()
        fake_scan.repos = [fake_repo]

        prompt_called = []

        def _fake_prompt_wiki_style(_console):
            prompt_called.append(True)
            return "comprehensive"

        with (
            patch(
                "repowise.core.workspace.scan_for_repos",
                return_value=fake_scan,
            ),
            patch(
                "repowise.cli.commands.init_cmd.command.prompt_wiki_style",
                side_effect=_fake_prompt_wiki_style,
            ),
            patch(
                "repowise.cli.commands.init_cmd.command.resolve_provider",
                side_effect=Exception("stop early"),
            ),
        ):
            runner.invoke(cli, ["init", "-y", "--index-only", str(tmp_path)])
            assert not prompt_called, "-y should bypass the wiki-style interactive prompt"

    def test_yes_skips_mode_select_on_tty(self, runner, tmp_path):
        """With -y on a TTY, the interactive mode-selection menu is never shown.

        Without the ``and not yes`` guard on ``is_interactive`` a scripted
        ``init -y`` would still block on ``interactive_mode_select`` whenever
        stdin happens to be a TTY.
        """
        from unittest.mock import MagicMock, patch

        fake_repo = MagicMock()
        fake_repo.path = tmp_path
        fake_scan = MagicMock()
        fake_scan.repos = [fake_repo]

        mode_select_called = []

        with (
            patch(
                "repowise.core.workspace.scan_for_repos",
                return_value=fake_scan,
            ),
            patch("sys.stdin.isatty", return_value=True),
            patch(
                "repowise.cli.commands.init_cmd.command.interactive_mode_select",
                side_effect=lambda *_a, **_kw: mode_select_called.append(True),
            ),
            # Stop before the real pipeline runs.
            patch(
                "repowise.cli.commands.init_cmd.command.resolve_provider",
                side_effect=Exception("stop early"),
            ),
        ):
            runner.invoke(cli, ["init", "-y", str(tmp_path)])
            assert not mode_select_called, (
                "-y must bypass the interactive mode-selection menu even on a TTY"
            )


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
            prior_defect_raw_count=9,
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
        assert d["prior_defect_raw_count"] == 9
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

        # _run_full_health_rescore now lives in update_cmd.persistence, which
        # is where it looks up run_async — patch the name there.
        monkeypatch.setattr(update_cmd.persistence, "run_async", _boom)
        (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")

        update_cmd._run_full_health_rescore(
            tmp_path, [], {"last_sync_commit": "base"}, "head1", "NEWFP"
        )

        state_file = tmp_path / ".repowise" / "state.json"
        if state_file.exists():
            assert json.loads(state_file.read_text()).get("config_fingerprint") != "NEWFP"


class TestBuildRepoGraph:
    """The shared traverse/parse/build helper used by both the incremental
    rebuild and the config-triggered re-score paths."""

    def test_reports_parse_skips_instead_of_swallowing(self, tmp_path, monkeypatch):
        """Files that fail to parse are skipped and surfaced as a count."""
        from repowise.cli.commands import update_cmd
        from repowise.core.ingestion import ASTParser

        (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "bad.py").write_text("y = 2\n", encoding="utf-8")

        real_parse = ASTParser.parse_file

        def _maybe_raise(self, fi, source):
            if fi.path.endswith("bad.py"):
                raise ValueError("boom")
            return real_parse(self, fi, source)

        monkeypatch.setattr(ASTParser, "parse_file", _maybe_raise)

        printed: list[str] = []

        class _FakeConsole:
            def print(self, *args, **kwargs):
                printed.append(" ".join(str(a) for a in args))

        # _build_repo_graph now lives in update_cmd.incremental, which is where
        # it looks up console — patch the name there.
        monkeypatch.setattr(update_cmd.incremental, "console", _FakeConsole())

        parsed_files, _src, _gb, _struct, _count = update_cmd._build_repo_graph(tmp_path, [])

        paths = [pf.file_info.path for pf in parsed_files]
        assert any(p.endswith("good.py") for p in paths)
        assert not any(p.endswith("bad.py") for p in paths)
        assert any("Skipped" in line for line in printed)

    def test_includes_framework_edge_step(self, tmp_path, monkeypatch):
        """The shared path always runs the framework-aware synthetic edge step,
        so the re-score graph matches the incremental rebuild graph."""
        from repowise.cli.commands import update_cmd
        from repowise.core.ingestion import GraphBuilder

        (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")

        monkeypatch.setattr(
            "repowise.core.generation.editor_files.tech_stack.detect_tech_stack",
            lambda _p: [],
        )

        calls: list = []
        real_add = GraphBuilder.add_framework_edges

        def _spy(self, names):
            calls.append(list(names))
            return real_add(self, names)

        monkeypatch.setattr(GraphBuilder, "add_framework_edges", _spy)

        update_cmd._build_repo_graph(tmp_path, [])

        assert calls, "framework-edge step must run in the shared rebuild path"

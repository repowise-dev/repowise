"""Unit tests for ``repowise workspace`` command group."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli

WORKSPACE_CONFIG_FILENAME = ".repowise-workspace.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


def _write_workspace_config(root: Path, repos: list[dict], default_repo: str | None = None) -> None:
    """Write a minimal .repowise-workspace.yaml to *root*."""
    data: dict = {
        "version": 1,
        "default_repo": default_repo or (repos[0]["alias"] if repos else None),
        "repos": repos,
    }
    (root / WORKSPACE_CONFIG_FILENAME).write_text(
        yaml.dump(data, default_flow_style=False),
        encoding="utf-8",
    )


def _read_workspace_config(root: Path) -> dict:
    text = (root / WORKSPACE_CONFIG_FILENAME).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _make_git_repo(path: Path) -> None:
    """Create a minimal fake git repo at *path*."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


# ---------------------------------------------------------------------------
# workspace list
# ---------------------------------------------------------------------------


class TestWorkspaceList:
    def test_list_shows_repos(self, runner, tmp_path):
        repo_a = tmp_path / "service-a"
        repo_b = tmp_path / "service-b"
        repo_a.mkdir()
        repo_b.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "service-a", "alias": "service-a"},
                {"path": "service-b", "alias": "service-b"},
            ],
            default_repo="service-a",
        )

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0
        assert "service-a" in result.output
        assert "service-b" in result.output

    def test_list_shows_summary_line(self, runner, tmp_path):
        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[{"path": "repo-a", "alias": "repo-a"}],
        )

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0
        # Summary line: "X/Y repos indexed"
        assert "repos indexed" in result.output

    def test_list_marks_primary_repo(self, runner, tmp_path):
        repo_a = tmp_path / "main-svc"
        repo_a.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[{"path": "main-svc", "alias": "main-svc", "is_primary": True}],
            default_repo="main-svc",
        )

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0
        assert "primary" in result.output

    def test_list_no_workspace_errors(self, runner, tmp_path):
        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        # Should exit non-zero or print a helpful error — no workspace found
        assert result.exit_code != 0 or "No .repowise-workspace.yaml" in result.output


# ---------------------------------------------------------------------------
# workspace add
# ---------------------------------------------------------------------------


class TestWorkspaceAdd:
    def test_add_new_repo(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        new_repo = tmp_path / "new-service"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "add", str(new_repo)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "new-service" in result.output

        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "new-service" in aliases

    def test_add_with_custom_alias(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        new_repo = tmp_path / "my-long-service-name"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "add", str(new_repo), "--alias", "svc"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "svc" in aliases

    def test_add_nonexistent_path_errors(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        bad_path = str(tmp_path / "does-not-exist")

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "add", bad_path])

        assert result.exit_code != 0

    def test_add_non_git_path_errors(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "add", str(plain_dir)])

        assert result.exit_code != 0
        assert ".git" in result.output

    def test_add_duplicate_alias_errors(self, runner, tmp_path):
        existing_repo = tmp_path / "svc"
        existing_repo.mkdir()
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc", "alias": "svc"}],
        )
        new_repo = tmp_path / "other-svc"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "add", str(new_repo), "--alias", "svc"],
            )

        assert result.exit_code != 0
        assert "svc" in result.output

    def test_add_no_workspace_errors(self, runner, tmp_path):
        # Create a subdirectory that has no workspace config anywhere in its tree
        isolated = tmp_path / "no-ws"
        isolated.mkdir()
        new_repo = isolated / "svc"
        _make_git_repo(new_repo)

        with runner.isolated_filesystem(temp_dir=str(isolated)):
            result = runner.invoke(cli, ["workspace", "add", str(new_repo)])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# workspace remove
# ---------------------------------------------------------------------------


class TestWorkspaceRemove:
    def test_remove_existing_repo(self, runner, tmp_path):
        repo_a = tmp_path / "svc-a"
        repo_a.mkdir()
        repo_b = tmp_path / "svc-b"
        repo_b.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "svc-a", "alias": "svc-a"},
                {"path": "svc-b", "alias": "svc-b"},
            ],
            default_repo="svc-a",
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "remove", "svc-b"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "svc-b" not in aliases
        assert "svc-a" in aliases

    def test_remove_default_repo_shows_warning(self, runner, tmp_path):
        repo_a = tmp_path / "svc-a"
        repo_a.mkdir()
        repo_b = tmp_path / "svc-b"
        repo_b.mkdir()

        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "svc-a", "alias": "svc-a"},
                {"path": "svc-b", "alias": "svc-b"},
            ],
            default_repo="svc-a",
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "remove", "svc-a"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        # Should mention the reassignment
        assert "default" in result.output.lower() or "Note" in result.output

    def test_remove_preserves_repowise_dir(self, runner, tmp_path):
        repo_a = tmp_path / "svc-a"
        repo_a.mkdir()
        repowise_dir = repo_a / ".repowise"
        repowise_dir.mkdir()
        sentinel = repowise_dir / "wiki.db"
        sentinel.write_text("data")

        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc-a", "alias": "svc-a"}],
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            runner.invoke(cli, ["workspace", "remove", "svc-a"])

        # Indexed data must still exist
        assert sentinel.exists()

    def test_remove_invalid_alias_errors(self, runner, tmp_path):
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc-a", "alias": "svc-a"}],
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "remove", "no-such-repo"])

        assert result.exit_code != 0
        assert "no-such-repo" in result.output

    def test_remove_no_workspace_errors(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "remove", "svc"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# workspace scan
# ---------------------------------------------------------------------------


class TestWorkspaceScan:
    def test_scan_discovers_new_repos(self, runner, tmp_path):
        # Workspace exists but has no repos registered
        _write_workspace_config(tmp_path, repos=[], default_repo=None)

        # Create a git repo inside the workspace root
        new_repo = tmp_path / "discovered-svc"
        _make_git_repo(new_repo)

        result = runner.invoke(
            cli,
            ["workspace", "scan", str(tmp_path), "--yes"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "discovered-svc" in result.output

        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "discovered-svc" in aliases

    def test_scan_skips_already_registered(self, runner, tmp_path):
        existing = tmp_path / "existing-svc"
        _make_git_repo(existing)
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "existing-svc", "alias": "existing-svc"}],
        )

        result = runner.invoke(
            cli,
            ["workspace", "scan", str(tmp_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "No new repositories" in result.output

    def test_scan_prompts_when_no_yes_flag(self, runner, tmp_path):
        _write_workspace_config(tmp_path, repos=[], default_repo=None)
        new_repo = tmp_path / "new-svc"
        _make_git_repo(new_repo)

        # Answer "n" to the prompt
        result = runner.invoke(
            cli,
            ["workspace", "scan", str(tmp_path)],
            input="n\n",
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        cfg = _read_workspace_config(tmp_path)
        aliases = [r["alias"] for r in cfg.get("repos", [])]
        assert "new-svc" not in aliases

    def test_scan_no_workspace_errors(self, runner, tmp_path):
        result = runner.invoke(cli, ["workspace", "scan", str(tmp_path)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# workspace set-default
# ---------------------------------------------------------------------------


class TestWorkspaceSetDefault:
    def test_set_default_changes_primary(self, runner, tmp_path):
        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "svc-a", "alias": "svc-a", "is_primary": True},
                {"path": "svc-b", "alias": "svc-b"},
            ],
            default_repo="svc-a",
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(
                cli,
                ["workspace", "set-default", "svc-b"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "svc-b" in result.output

        cfg = _read_workspace_config(tmp_path)
        assert cfg["default_repo"] == "svc-b"

        # is_primary flag should be set only on svc-b
        by_alias = {r["alias"]: r for r in cfg["repos"]}
        assert by_alias["svc-b"].get("is_primary") is True
        assert not by_alias["svc-a"].get("is_primary", False)

    def test_set_default_invalid_alias_errors(self, runner, tmp_path):
        _write_workspace_config(
            tmp_path,
            repos=[{"path": "svc-a", "alias": "svc-a"}],
        )

        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "set-default", "nonexistent"])

        assert result.exit_code != 0
        assert "nonexistent" in result.output

    def test_set_default_no_workspace_errors(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            result = runner.invoke(cli, ["workspace", "set-default", "svc-a"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Help smoke tests
# ---------------------------------------------------------------------------


class TestWorkspaceHelp:
    def test_workspace_help(self, runner):
        result = runner.invoke(cli, ["workspace", "--help"])
        assert result.exit_code == 0
        assert "workspace" in result.output

    def test_workspace_list_help(self, runner):
        result = runner.invoke(cli, ["workspace", "list", "--help"])
        assert result.exit_code == 0

    def test_workspace_add_help(self, runner):
        result = runner.invoke(cli, ["workspace", "add", "--help"])
        assert result.exit_code == 0
        assert "--alias" in result.output
        assert "--index" in result.output

    def test_workspace_remove_help(self, runner):
        result = runner.invoke(cli, ["workspace", "remove", "--help"])
        assert result.exit_code == 0

    def test_workspace_scan_help(self, runner):
        result = runner.invoke(cli, ["workspace", "scan", "--help"])
        assert result.exit_code == 0
        assert "--yes" in result.output

    def test_workspace_set_default_help(self, runner):
        result = runner.invoke(cli, ["workspace", "set-default", "--help"])
        assert result.exit_code == 0

"""Tests for MCP workspace member detection."""

from pathlib import Path

import pytest

from repowise.server.mcp_server._server import _detect_workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace with a root repo and two declared members."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "test-repos" / "microdot").mkdir(parents=True)

    (tmp_path / ".repowise-workspace.yaml").write_text(
        "version: 1\n"
        "default_repo: repowise\n"
        "repos:\n"
        "- path: .\n"
        "  alias: repowise\n"
        "  is_primary: true\n"
        "- path: backend\n"
        "  alias: backend\n"
        "- path: frontend\n"
        "  alias: frontend\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(
    ("member_path", "expected_alias"),
    [
        ("backend", "backend"),
        ("frontend", "frontend"),
    ],
)
def test_detect_workspace_prefers_most_specific_member(
    workspace: Path,
    member_path: str,
    expected_alias: str,
) -> None:
    ws_root, ws_config, repo_alias = _detect_workspace(str(workspace / member_path))

    assert ws_root == workspace.resolve()
    assert ws_config is not None
    assert repo_alias == expected_alias


def test_detect_workspace_root_uses_primary(workspace: Path) -> None:
    _, _, repo_alias = _detect_workspace(str(workspace))

    assert repo_alias == "repowise"


def test_detect_workspace_non_member_bare_dir_uses_primary(workspace: Path) -> None:
    """A plain subdirectory (no .repowise/ of its own) is not a repo at all,
    so containment correctly falls through to the primary."""
    _, _, repo_alias = _detect_workspace(str(workspace / "test-repos" / "microdot"))
    assert repo_alias == "repowise"


def test_detect_workspace_non_member_indexed_repo_drops_to_single_repo(
    workspace: Path,
) -> None:
    """A path that is itself an indexed repo (has .repowise/state.json) but
    isn't a registered workspace member must not silently serve the
    enclosing primary, it should drop to single-repo mode instead."""
    nested = workspace / "test-repos" / "microdot"
    (nested / ".repowise").mkdir(parents=True, exist_ok=True)
    (nested / ".repowise" / "state.json").write_text("{}", encoding="utf-8")

    ws_root, ws_config, repo_alias = _detect_workspace(str(nested))

    assert ws_root is None
    assert ws_config is None
    assert repo_alias is None

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


def test_detect_workspace_non_member_uses_primary(workspace: Path) -> None:
    _, _, repo_alias = _detect_workspace(str(workspace / "test-repos" / "microdot"))

    assert repo_alias == "repowise"

"""MCP coverage for live commit/range change-risk scoring."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


@pytest.mark.asyncio
async def test_get_change_risk_honors_riskignore_and_request_filters(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(
        repo,
        {
            "src/app.py": "value = 1\n",
            "tests/test_app.py": "def test_value():\n    assert True\n",
            "docs/notes.md": "notes\n",
        },
        "feat: app",
    )
    (repo / ".riskignore").write_text("tests/\n", encoding="utf-8")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(
        extensions=["py", "md"], exclude_patterns=["docs/"], baseline=0
    )

    assert result["features"] == {
        "la": 1,
        "ld": 0,
        "nf": 1,
        "nd": 1,
        "ns": 1,
        "entropy": 0.0,
        "exp": 1,
    }
    assert result["exclude_patterns"] == ["tests/", "docs/"]
    assert result["risk_percentile"] is None
    assert result["review_priority"] is None
    assert result["classification"] is None
    assert result["baseline_sample_size"] == 0
    # Live-git responses carry a _meta envelope flagged as index-independent.
    assert result["_meta"]["source"] == "live_git"
    assert "warning" not in result


@pytest.mark.asyncio
async def test_get_change_risk_bad_revspec_returns_error(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(revspec="does-not-exist", baseline=0)

    # A bogus revspec must surface an error, not a silent zero-risk score.
    assert "error" in result
    assert "does-not-exist" in result["error"]
    assert "score" not in result


@pytest.mark.asyncio
async def test_get_change_risk_empty_diff_warns(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(repo, {"app.py": "value = 1\n"}, "feat: app")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    # Only a .py change exists; restricting to .md counts zero files.
    result = await module.get_change_risk(extensions=["md"], baseline=0)

    assert result["features"]["nf"] == 0
    assert "warning" in result
    assert "no counted file changes" in result["warning"].lower()


@pytest.mark.asyncio
async def test_get_change_risk_rejects_repo_all() -> None:
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")
    result = await module.get_change_risk(repo="all")

    assert "error" in result
    assert "get_change_risk" in result["error"]

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

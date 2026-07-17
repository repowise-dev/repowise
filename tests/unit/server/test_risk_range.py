"""Tests for the live base..head change-risk endpoint."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.analysis.change_risk import baseline_scores
from tests.unit.server.conftest import create_test_repo


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str, author: str = "Dev") -> str:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(["add", rel], repo)
    _git(["-c", f"user.name={author}", "-c", "user.email=t@e.com", "commit", "-m", message], repo)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real, tiny git repo at ``tmp_path/test-repo`` (the name expected by
    :func:`create_test_repo`), with a few commits scored to a base..head
    range."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.name", "Seed"], repo)
    _git(["config", "user.email", "seed@e.com"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed", author="Seed")
    return repo


async def _register(client: AsyncClient, tmp_path: Path) -> dict:
    return await create_test_repo(client, tmp_path)


@pytest.mark.asyncio
async def test_risk_range_happy_path(client: AsyncClient, git_repo: Path, tmp_path: Path) -> None:
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(git_repo, {"src/a.py": "x = 1\ny = 2\n"}, "feat: add a")
    _commit(git_repo, {"src/b.py": "z = 3\n"}, "fix: handle null crash")

    repo = await _register(client, tmp_path)
    resp = await client.get(
        f"/api/repos/{repo['id']}/risk/range",
        params={"base": base, "head": "HEAD"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["base"] == base
    assert data["head"] == "HEAD"
    assert 0.0 <= data["score"] <= 10.0
    assert 0.0 <= data["probability"] <= 1.0
    assert data["level"] in {"low", "moderate", "high"}
    assert data["is_fix"] is True
    assert data["features"]["nf"] == 2
    assert data["features"]["la"] == 3
    assert isinstance(data["drivers"], list) and len(data["drivers"]) > 0
    # Only two commits sampled, below _MIN_BASELINE, so no percentile.
    assert data["risk_percentile"] is None
    assert data["review_priority"] is None


@pytest.mark.asyncio
async def test_risk_range_unknown_repo(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/repos/does-not-exist/risk/range",
        params={"base": "HEAD~1", "head": "HEAD"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_risk_range_bad_revspec(client: AsyncClient, git_repo: Path, tmp_path: Path) -> None:
    repo = await _register(client, tmp_path)
    resp = await client.get(
        f"/api/repos/{repo['id']}/risk/range",
        params={"base": "totally-bogus-rev", "head": "HEAD"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_risk_range_baseline_zero_skips_percentile(
    client: AsyncClient, git_repo: Path, tmp_path: Path
) -> None:
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(git_repo, {"src/c.py": "a = 1\n"}, "feat: add c")

    repo = await _register(client, tmp_path)
    resp = await client.get(
        f"/api/repos/{repo['id']}/risk/range",
        params={"base": base, "head": "HEAD", "baseline": 0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["risk_percentile"] is None
    assert data["review_priority"] is None


def test_baseline_scores_returns_floats(git_repo: Path) -> None:
    _commit(git_repo, {"src/d.py": "a = 1\nb = 2\n"}, "feat: add d")
    _commit(git_repo, {"src/e.py": "c = 3\n"}, "fix: crash on null")

    scores = baseline_scores(str(git_repo), "HEAD", 200, (), excluded_ref="")
    assert len(scores) >= 1
    assert all(isinstance(s, float) for s in scores)


def test_baseline_scores_filters_excluded_paths(git_repo: Path) -> None:
    _commit(git_repo, {"src/app.py": "a = 1\n"}, "feat: app")
    _commit(
        git_repo,
        {"tests/test_app.py": "\n".join(f"assert {i}" for i in range(30)) + "\n"},
        "test: app",
    )

    scores = baseline_scores(
        str(git_repo), "HEAD", 2, (), excluded_ref="", exclude_patterns=("tests/",)
    )

    assert len(scores) == 1


def test_baseline_scores_omits_target_ref(git_repo: Path) -> None:
    _commit(git_repo, {"src/app.py": "a = 1\n"}, "feat: app")
    head = _commit(git_repo, {"src/next.py": "b = 2\n"}, "feat: next")

    scores = baseline_scores(str(git_repo), "HEAD", 2, (), excluded_ref=head)

    assert len(scores) == 1

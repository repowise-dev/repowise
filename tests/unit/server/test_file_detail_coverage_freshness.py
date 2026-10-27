"""File detail ``coverage.freshness``: do the stored covered lines still fit the file on disk?

The file page paints ``covered_lines`` onto the source it reads from disk, so
a file that changed after the measured commit must say ``stale``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.analysis.patch_coverage import file_on_disk_freshness
from repowise.core.persistence.crud import save_coverage_files, save_health_metrics
from tests.unit.server.conftest import create_test_repo

_PATH = "src/a.py"


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, content: str, message: str) -> str:
    (repo / _PATH).parent.mkdir(parents=True, exist_ok=True)
    (repo / _PATH).write_text(content, encoding="utf-8")
    _git(["add", _PATH], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=t@e.com", "commit", "-q", "-m", message], repo)
    return _git(["rev-parse", "HEAD"], repo)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "test-repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    return repo


async def _detail(client: AsyncClient, session, tmp_path: Path, commit: str | None) -> dict:
    repo = await create_test_repo(client, tmp_path)
    files = [
        {
            "file_path": _PATH,
            "line_coverage_pct": 50.0,
            "covered_lines": [1],
            "total_coverable_lines": 2,
            "branch_coverage_pct": None,
        }
    ]
    await save_coverage_files(
        session, repo["id"], files, source_format="lcov", ingested_commit_sha=commit
    )
    await save_health_metrics(
        session,
        repo["id"],
        [{"file_path": _PATH, "score": 9.0, "max_ccn": 1, "max_nesting": 1, "nloc": 2}],
    )
    await session.commit()
    resp = await client.get(f"/api/repos/{repo['id']}/files/{_PATH}")
    assert resp.status_code == 200
    return resp.json()["coverage"]


@pytest.mark.asyncio
async def test_current_when_file_unchanged_since_measured_commit(
    client: AsyncClient, session, git_repo: Path, tmp_path: Path
) -> None:
    measured = _commit(git_repo, "x = 1\ny = 2\n", "feat: add a")
    coverage = await _detail(client, session, tmp_path, measured)
    assert coverage["freshness"] == "current"
    assert coverage["covered_lines"] == [1]


@pytest.mark.asyncio
async def test_stale_when_file_committed_after_measured_commit(
    client: AsyncClient, session, git_repo: Path, tmp_path: Path
) -> None:
    measured = _commit(git_repo, "x = 1\ny = 2\n", "feat: add a")
    _commit(git_repo, "w = 0\nx = 1\ny = 2\n", "feat: grow a")
    coverage = await _detail(client, session, tmp_path, measured)
    assert coverage["freshness"] == "stale"


@pytest.mark.asyncio
async def test_stale_when_file_edited_on_disk_after_measured_commit(
    client: AsyncClient, session, git_repo: Path, tmp_path: Path
) -> None:
    measured = _commit(git_repo, "x = 1\ny = 2\n", "feat: add a")
    (git_repo / _PATH).write_text("w = 0\nx = 1\ny = 2\n", encoding="utf-8")
    coverage = await _detail(client, session, tmp_path, measured)
    assert coverage["freshness"] == "stale"


@pytest.mark.asyncio
async def test_unknown_without_a_measured_commit(
    client: AsyncClient, session, git_repo: Path, tmp_path: Path
) -> None:
    _commit(git_repo, "x = 1\ny = 2\n", "feat: add a")
    coverage = await _detail(client, session, tmp_path, None)
    assert coverage["freshness"] == "unknown"


@pytest.mark.asyncio
async def test_unknown_for_a_commit_the_clone_lacks(
    client: AsyncClient, session, git_repo: Path, tmp_path: Path
) -> None:
    _commit(git_repo, "x = 1\ny = 2\n", "feat: add a")
    coverage = await _detail(client, session, tmp_path, "0" * 40)
    assert coverage["freshness"] == "unknown"


@pytest.mark.asyncio
async def test_paths_resolve_against_a_checkout_below_the_git_top_level(tmp_path: Path) -> None:
    top = tmp_path / "mono"
    (top / "svc").mkdir(parents=True)
    _git(["init", "-q"], top)
    (top / "svc" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "svc/a.py"], top)
    _git(["-c", "user.name=Dev", "-c", "user.email=t@e.com", "commit", "-q", "-m", "seed"], top)
    measured = _git(["rev-parse", "HEAD"], top)
    sub = str(top / "svc")
    assert await file_on_disk_freshness(sub, measured, "a.py") == "current"
    (top / "svc" / "a.py").write_text("w = 0\nx = 1\n", encoding="utf-8")
    assert await file_on_disk_freshness(sub, measured, "a.py") == "stale"
    (top / "svc" / "b.py").write_text("new\n", encoding="utf-8")
    assert await file_on_disk_freshness(sub, measured, "b.py") == "stale"

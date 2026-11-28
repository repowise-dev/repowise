"""``/health/coverage/patch`` for an index built below the git top level.

The index names files from its own checkout (``src/a.py``) while git names
them from the top level (``svc/src/a.py``); a changed covered file must still
match its stored coverage.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.analysis.health.coverage import file_coverage
from repowise.core.persistence.crud import save_coverage_files, upsert_repository


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_changed_covered_file_reads_measured(
    client: AsyncClient, session, tmp_path: Path
) -> None:
    top = tmp_path / "mono"
    (top / "svc" / "src").mkdir(parents=True)
    _git(top, "init", "-q")
    _git(top, "config", "user.email", "t@t.co")
    _git(top, "config", "user.name", "t")
    (top / "svc" / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(top, "add", "-A")
    _git(top, "commit", "-qm", "base")
    base = _git(top, "rev-parse", "HEAD")
    (top / "svc" / "src" / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    _git(top, "commit", "-qam", "grow a")
    head = _git(top, "rev-parse", "HEAD")

    repo = await upsert_repository(session, name="svc", local_path=str(top / "svc"))
    await save_coverage_files(
        session,
        repo.id,
        [file_coverage("src/a.py", [1, 2], [1, 2, 3])],
        source_format="lcov",
        ingested_commit_sha=head,
    )
    await session.commit()

    resp = await client.get(f"/api/repos/{repo.id}/health/coverage/patch", params={"base": base})

    assert resp.status_code == 200
    data = resp.json()
    assert [(f["file_path"], f["status"]) for f in data["files"]] == [("src/a.py", "measured")]
    assert data["files"][0]["uncovered_ranges"] == [[3, 3]]

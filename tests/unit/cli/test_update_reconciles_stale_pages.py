"""``repowise update`` reconciles stale file pages when HEAD has not moved (#1744).

The early exit gates on git alone, and the "no changed files" exit right after
it does too. A page marked stale by a budget-capped cascade, an interrupted
run or an expiry therefore had no command that could clear it. These drive
the real command over a one-file repository and read the page back.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from sqlalchemy import select


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def _db_url(repo: Path) -> str:
    return f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}"


async def _seed(repo: Path, pages: dict[str, str]) -> None:
    """A repository row plus one file page per ``{path: freshness_status}``."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page,
        upsert_repository,
    )

    engine = create_engine(_db_url(repo))
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        row = await upsert_repository(session, name=repo.name, local_path=str(repo))
        for path, status in pages.items():
            await upsert_page(
                session,
                page_id=f"file_page:{path}",
                repository_id=row.id,
                page_type="file_page",
                title=f"File: {path}",
                content=f"# {path}\n",
                summary="",
                target_path=path,
                source_hash="",
                model_name="template",
                provider_name="template",
                freshness_status=status,
            )
        await session.commit()
    await engine.dispose()


async def _freshness(repo: Path) -> dict[str, str]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(select(Page.target_path, Page.freshness_status))
            return dict(rows.all())
    finally:
        await engine.dispose()


def _repo_at_head(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "c0")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise").mkdir()
    return repo, head


def _update(repo: Path) -> str:
    from click.testing import CliRunner

    from repowise.cli.main import cli

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    return result.output


def _state_at(repo: Path, head: str) -> None:
    from repowise.cli.helpers import save_state

    save_state(
        repo,
        {"last_sync_commit": head, "last_docs_commit": head, "docs_mode": "deterministic"},
    )


def test_stale_page_for_a_present_file_is_re_rendered(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, {"foo.py": "stale"}))
    _state_at(repo, head)

    output = _update(repo)

    assert "Already up to date" not in output
    assert "Reconciling stale structural pages: 1" in output
    assert asyncio.run(_freshness(repo))["foo.py"] == "fresh"
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["last_sync_commit"] == head


def test_stale_page_for_a_missing_file_is_tombstoned(tmp_path: Path, monkeypatch) -> None:
    """Re-rendering cannot clear it; the deletion path can."""
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, {"foo.py": "fresh", "gone.py": "stale"}))
    _state_at(repo, head)

    output = _update(repo)

    assert "Stale pages for deleted files: 1" in output
    freshness = asyncio.run(_freshness(repo))
    assert freshness["gone.py"] == "tombstone"
    assert freshness["foo.py"] == "fresh"


def test_no_stale_pages_keeps_the_fast_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, {"foo.py": "fresh"}))
    _state_at(repo, head)

    output = _update(repo)

    assert "Already up to date" in output
    assert "Reconciling" not in output

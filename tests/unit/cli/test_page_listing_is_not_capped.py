"""``status`` and ``export`` cover every page, not the first ten thousand.

Both read the wiki through ``list_pages(..., limit=10000)``. ``list_pages`` is
the paginated listing helper whose ``limit`` defaults to 100, so passing 10000
raises the cap rather than removing it, and both commands silently stopped
there:

- ``status`` printed a correct total in its header, from a different query, and
  a per-type table underneath it that summed to exactly 10000. The token total
  was truncated with it.
- ``export`` wrote an archive missing every page past the 10000th, with nothing
  in the output to say so.

The same fetch backed ``doctor``'s two store reconciliations, where it turned
every page past the cap into a phantom orphan that ``--repair`` then deleted.
That half is pinned in ``test_doctor_page_cap.py``.
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

from repowise.cli.commands import status_cmd

PAGE_TOTAL = 10001


async def _build_repo(tmp_path: Path) -> Path:
    """A repository with one more page than the old cap could return."""
    import git as gitpython
    from sqlalchemy import insert

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import Page

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()

    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        # Bulk insert: the point is the row count, and 10001 upserts through the
        # CRUD layer would make this slow enough that nobody runs it.
        await session.execute(
            insert(Page),
            [
                {
                    "created_at": base + datetime.timedelta(seconds=i),
                    "updated_at": base + datetime.timedelta(seconds=i),
                    "id": f"file_page:f{i}.py",
                    "repository_id": repo.id,
                    "page_type": "file_page",
                    "title": f"File: f{i}.py",
                    "content": "Body.",
                    "summary": "",
                    "target_path": f"f{i}.py",
                    "source_hash": "",
                    "model_name": "mock",
                    "provider_name": "mock",
                    "freshness_status": "fresh",
                    "input_tokens": 1,
                    "output_tokens": 2,
                }
                for i in range(PAGE_TOTAL)
            ],
        )
        await session.commit()
    await engine.dispose()
    return repo_path


def test_status_counts_every_page_and_its_tokens(tmp_path: Path) -> None:
    """It reported exactly 10000 file pages, and the tokens of only those."""
    repo_path = asyncio.run(_build_repo(tmp_path))

    counts, total_tokens = asyncio.run(status_cmd._query_pages(repo_path))

    assert counts["file_page"] == PAGE_TOTAL
    assert total_tokens == PAGE_TOTAL * 3


def test_export_walks_past_the_old_cap(tmp_path: Path) -> None:
    """A full export stopped at 10000 pages and said nothing about it."""
    from click.testing import CliRunner

    from repowise.cli.commands.export_cmd import export_command

    repo_path = asyncio.run(_build_repo(tmp_path))
    out_dir = tmp_path / "export"

    result = CliRunner().invoke(
        export_command,
        [str(repo_path), "--format", "json", "--output", str(out_dir)],
    )

    assert result.exit_code == 0, result.output

    written = list(out_dir.rglob("*.json"))
    assert written, f"nothing exported: {result.output}"
    if len(written) == 1:
        import json

        payload = json.loads(written[0].read_text())
        pages = payload["pages"] if isinstance(payload, dict) else payload
        assert len(pages) == PAGE_TOTAL
    else:
        assert len(written) == PAGE_TOTAL

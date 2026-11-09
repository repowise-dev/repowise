"""A native CLI reads exact archived records from an existing repo-local store."""

from __future__ import annotations

import asyncio
import json

from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.persistence import crud
from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from tests.unit.persistence.helpers import make_page_kwargs


def test_retired_cli_reads_existing_store_and_history(tmp_path):
    from repowise.cli.helpers import get_db_url_for_repo

    repo_path = tmp_path / "demo"
    repo_path.mkdir()
    (repo_path / ".repowise").mkdir()

    async def seed():
        engine = create_engine(get_db_url_for_repo(repo_path))
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                repo = await crud.upsert_repository(session, name="demo", local_path=str(repo_path))
                kwargs = make_page_kwargs(repo.id, content="Previous CLI prose")
                await crud.upsert_page(session, **kwargs)
                await crud.upsert_page(
                    session, **{**kwargs, "content": "Current retired CLI prose"}
                )
                await crud.retire_pages(session, repo.id, [kwargs["page_id"]], "deselected")
        finally:
            await engine.dispose()

    asyncio.run(seed())
    args = ["pages", "retired", "--path", str(repo_path), "--json"]
    listed = CliRunner().invoke(cli, args)
    assert listed.exit_code == 0, listed.output
    assert [row["page_id"] for row in json.loads(listed.output)] == ["file_page:src/main.py"]
    detail = CliRunner().invoke(cli, [*args, "--page-id", "file_page:src/main.py"])
    assert detail.exit_code == 0, detail.output
    data = json.loads(detail.output)
    assert data["page"]["content"] == "Current retired CLI prose"
    assert [v["content"] for v in data["versions"]] == ["Previous CLI prose"]


def test_retired_cli_refuses_unindexed_repo_without_creating_store(tmp_path):
    result = CliRunner().invoke(cli, ["pages", "retired", "--path", str(tmp_path), "--json"])
    assert result.exit_code != 0
    assert "not indexed" in result.output.lower()
    assert not (tmp_path / ".repowise").exists()

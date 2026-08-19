"""``repowise init`` tombstones a page whose file is gone.

The helper doing the work is unit-tested next door. This drives the CLI
persistence path instead, because the helper existing is not the fix — being
called from both of ``persist_result``'s branches is. The normal single-repo
init persists the index phase during the run and so takes the
``index_persisted_incrementally=True`` branch, which never reached the
diff-driven tombstone path at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from repowise.cli._repo_session import open_repo_db
from repowise.cli.commands.init_cmd.persistence import persist_result
from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence import FullTextSearch, get_session
from repowise.core.persistence.models import Page


def _generated_page(page_type: str, target: str) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=f"{page_type}:{target}",
        page_type=page_type,
        title=target,
        content=f"content for {target}",
        source_hash="x" * 64,
        model_name="mock",
        provider_name="mock",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        generation_level=1,
        target_path=target,
        created_at=now,
        updated_at=now,
    )


def _result(repo_name: str, generated_pages: list[GeneratedPage]) -> SimpleNamespace:
    return SimpleNamespace(
        repo_name=repo_name,
        index_persisted_incrementally=True,
        generated_pages=generated_pages,
        tech_stack=None,
        vector_store=None,
        dead_code_report=None,
        health_report=None,
        decision_report=None,
        git_metadata_list=[],
        knowledge_graph_result=None,
        authoritative_page_types=set(),
        preserved_page_ids=set(),
    )


async def _seed_file_pages(repo_path, *paths: str) -> str:
    """A fresh file page per path, seeded as a previous run would leave it."""
    engine, sf, repo_id = await open_repo_db(repo_path, repo_name="r")
    try:
        now = datetime.now(UTC)
        async with get_session(sf) as session:
            for path in paths:
                session.add(
                    Page(
                        id=f"file_page:{path}",
                        repository_id=repo_id,
                        page_type="file_page",
                        title=f"File: {path}",
                        content=f"# Overview\n\n{path} handles xylophone tuning.",
                        target_path=path,
                        source_hash="x" * 64,
                        model_name="mock",
                        provider_name="mock",
                        created_at=now,
                        updated_at=now,
                    )
                )
    finally:
        await engine.dispose()
    return repo_id


async def _status(repo_path, page_id: str) -> str:
    engine, sf, _ = await open_repo_db(repo_path, repo_name="r")
    try:
        async with get_session(sf) as session:
            res = await session.execute(select(Page).where(Page.id == page_id))
            return res.scalar_one().freshness_status
    finally:
        await engine.dispose()


async def test_init_tombstones_the_page_of_a_deleted_file(tmp_path):
    """No diff anywhere in this run — just a page whose file is not there."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "kept.py").write_text("x", encoding="utf-8")
    await _seed_file_pages(repo_path, "kept.py", "gone.py")

    await persist_result(_result("r", [_generated_page("module_page", "community-75")]), repo_path)

    assert await _status(repo_path, "file_page:gone.py") == "tombstone"
    assert await _status(repo_path, "file_page:kept.py") == "fresh"


async def test_the_tombstoned_page_leaves_the_full_text_index(tmp_path):
    """A tombstone still occupies one of the fixed number of rows retrieval
    fetches before any filter runs, so the row has to go, not just the flag."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "kept.py").write_text("x", encoding="utf-8")
    await _seed_file_pages(repo_path, "kept.py", "gone.py")

    engine, _sf, _ = await open_repo_db(repo_path, repo_name="r")
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    for path in ("kept.py", "gone.py"):
        await fts.index(f"file_page:{path}", path, f"{path} handles xylophone tuning.")
    await engine.dispose()

    await persist_result(_result("r", [_generated_page("module_page", "community-75")]), repo_path)

    engine, _sf, _ = await open_repo_db(repo_path, repo_name="r")
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    hits = {r.page_id for r in await fts.search("xylophone", limit=10)}
    await engine.dispose()

    assert "file_page:gone.py" not in hits
    assert "file_page:kept.py" in hits

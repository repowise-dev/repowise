"""Page version history is held to ``wiki.page_versions_keep`` per page."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from repowise.core.persistence.crud import (
    DEFAULT_PAGE_VERSIONS_KEEP,
    get_page,
    get_page_versions,
    page_versions_keep,
    prune_page_versions,
    upsert_page,
    upsert_pages_from_generated,
)
from repowise.core.persistence.models import PageVersion
from repowise.core.settings import get as get_setting
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


def _write_keep(root: Path, keep: object) -> None:
    (root / ".repowise").mkdir(parents=True, exist_ok=True)
    (root / ".repowise" / "config.yaml").write_text(
        f"wiki:\n  page_versions_keep: {keep}\n", encoding="utf-8"
    )


async def _rewrite(session, kwargs: dict, times: int, *, start: int = 0) -> None:
    for i in range(start, start + times):
        await upsert_page(session, **dict(kwargs, content=f"body {i}", source_hash=f"h{i}"))
        await session.commit()


async def _count(session, page_id: str | None = None) -> int:
    query = select(func.count()).select_from(PageVersion)
    if page_id is not None:
        query = query.where(PageVersion.page_id == page_id)
    return (await session.execute(query)).scalar_one()


def test_the_default_matches_the_registered_setting():
    assert get_setting("wiki.page_versions_keep").default == DEFAULT_PAGE_VERSIONS_KEEP


def test_keep_reads_the_repo_config(tmp_path):
    assert page_versions_keep(None) == DEFAULT_PAGE_VERSIONS_KEEP
    assert page_versions_keep(tmp_path) == DEFAULT_PAGE_VERSIONS_KEEP
    _write_keep(tmp_path, 3)
    assert page_versions_keep(tmp_path) == 3
    for refused in (0, -2, "many"):
        _write_keep(tmp_path, refused)
        assert page_versions_keep(tmp_path) == DEFAULT_PAGE_VERSIONS_KEEP
    (tmp_path / ".repowise" / "config.yaml").write_text("wiki: [unclosed\n", encoding="utf-8")
    assert page_versions_keep(tmp_path) == DEFAULT_PAGE_VERSIONS_KEEP


async def test_a_rewrite_keeps_only_the_newest_versions(async_session):
    repo = await insert_repo(async_session)
    kwargs = make_page_kwargs(repo.id)
    await _rewrite(async_session, kwargs, DEFAULT_PAGE_VERSIONS_KEEP + 6)

    versions = await get_page_versions(async_session, kwargs["page_id"])
    page = await get_page(async_session, kwargs["page_id"])
    assert len(versions) == DEFAULT_PAGE_VERSIONS_KEEP
    # The newest ones survive, ending right below the live page.
    assert [v.version for v in versions] == list(
        range(page.version - 1, page.version - 1 - DEFAULT_PAGE_VERSIONS_KEEP, -1)
    )
    assert page.content == f"body {DEFAULT_PAGE_VERSIONS_KEEP + 5}"


async def test_the_repo_config_sets_the_cap(async_session, tmp_path):
    _write_keep(tmp_path, 2)
    repo = await insert_repo(async_session, local_path=str(tmp_path))
    kwargs = make_page_kwargs(repo.id)
    other = make_page_kwargs(repo.id, page_id="file_page:src/other.py", target_path="src/other.py")
    await _rewrite(async_session, kwargs, 6)
    await _rewrite(async_session, other, 4)

    assert [v.version for v in await get_page_versions(async_session, kwargs["page_id"])] == [5, 4]
    assert await _count(async_session, other["page_id"]) == 2


async def test_an_unchanged_write_neither_snapshots_nor_trims(async_session, tmp_path):
    repo = await insert_repo(async_session, local_path=str(tmp_path))
    kwargs = make_page_kwargs(repo.id)
    await _rewrite(async_session, kwargs, 5)
    _write_keep(tmp_path, 1)
    # Same content and hash as the last write: the touch path, no new version.
    await upsert_page(async_session, **dict(kwargs, content="body 4", source_hash="h4"))
    await async_session.commit()
    assert await _count(async_session) == 4


async def test_the_batch_write_trims_every_page_it_snapshotted(async_session, tmp_path):
    _write_keep(tmp_path, 2)
    repo = await insert_repo(async_session, local_path=str(tmp_path))
    ids = [f"file_page:src/m{i}.py" for i in range(3)]

    def generated(i: int, page_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            page_id=page_id,
            page_type="file_page",
            title=page_id,
            content=f"body {i}",
            target_path=page_id.split(":", 1)[1],
            source_hash=f"h{i}",
            model_name="mock",
            provider_name="mock",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            generation_level=0,
            confidence=1.0,
            freshness_status="fresh",
            metadata={},
            created_at="2026-11-27T00:00:00Z",
            updated_at="2026-11-27T00:00:00Z",
        )

    for i in range(5):
        await upsert_pages_from_generated(
            async_session, [generated(i, pid) for pid in ids], repo.id
        )
        await async_session.commit()

    for pid in ids:
        assert [v.version for v in await get_page_versions(async_session, pid)] == [4, 3]


async def test_a_full_prune_trims_old_history_and_is_idempotent(async_session, tmp_path):
    repo = await insert_repo(async_session, local_path=str(tmp_path))
    kwargs = make_page_kwargs(repo.id)
    other = make_page_kwargs(repo.id, page_id="file_page:src/other.py", target_path="src/other.py")
    await _rewrite(async_session, kwargs, 8)
    await _rewrite(async_session, other, 3)
    assert await _count(async_session) == 7 + 2

    assert await prune_page_versions(async_session, 3, repository_id=repo.id) == 4
    await async_session.commit()
    assert [v.version for v in await get_page_versions(async_session, kwargs["page_id"])] == [
        7,
        6,
        5,
    ]
    assert await _count(async_session, other["page_id"]) == 2

    assert await prune_page_versions(async_session, 3, repository_id=repo.id) == 0
    # The live pages are untouched.
    assert (await get_page(async_session, kwargs["page_id"])).content == "body 7"
    assert (await get_page(async_session, other["page_id"])).content == "body 2"


async def test_a_full_prune_leaves_other_repositories_alone(async_session, tmp_path):
    first = await insert_repo(async_session, local_path=str(tmp_path / "a"))
    second = await insert_repo(async_session, name="b", local_path=str(tmp_path / "b"))
    a = make_page_kwargs(first.id, page_id="file_page:a.py")
    b = make_page_kwargs(second.id, page_id="file_page:b.py")
    await _rewrite(async_session, a, 5)
    await _rewrite(async_session, b, 5)

    await prune_page_versions(async_session, 1, repository_id=first.id)
    await async_session.commit()
    assert await _count(async_session, a["page_id"]) == 1
    assert await _count(async_session, b["page_id"]) == 4

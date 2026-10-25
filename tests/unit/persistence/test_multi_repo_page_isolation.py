"""Unit tests verifying multi-repo page isolation in a shared database.

When multiple repositories share a database (e.g. PostgreSQL or a multi-repo workspace),
pages with identical relative paths (and identical page_id values like file_page:src/main.py)
must never collide, overwrite, or corrupt each other's version histories or sweeps.
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence.crud import (
    delete_repository,
    get_page,
    get_page_versions,
    list_pages,
    upsert_page,
    upsert_repository,
)
from repowise.core.persistence.crud.pages import upsert_pages_from_generated
from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import _sweep_stale_generated_pages
from tests.unit.persistence.helpers import make_page_kwargs, make_repo_kwargs


async def test_upsert_page_same_path_different_repos_isolation(async_session):
    """Two repos with the same relative file path have distinct independent pages."""
    repo1 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo1", local_path="/tmp/repo1")
    )
    repo2 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo2", local_path="/tmp/repo2")
    )
    await async_session.commit()

    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo1.id,
            page_id="file_page:src/main.py",
            title="Repo 1 Main",
            content="Repo 1 Content",
        ),
    )
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo2.id,
            page_id="file_page:src/main.py",
            title="Repo 2 Main",
            content="Repo 2 Content",
        ),
    )
    await async_session.commit()

    # Both pages exist in the DB
    all_pages = (await async_session.execute(select(Page))).scalars().all()
    assert len(all_pages) == 2

    # Scoped get_page retrieves the correct repo's page
    p1 = await get_page(async_session, "file_page:src/main.py", repository_id=repo1.id)
    p2 = await get_page(async_session, "file_page:src/main.py", repository_id=repo2.id)

    assert p1 is not None and p1.repository_id == repo1.id and p1.title == "Repo 1 Main"
    assert p2 is not None and p2.repository_id == repo2.id and p2.title == "Repo 2 Main"


async def test_page_versioning_is_scoped_to_repository(async_session):
    """Updating a page in repo1 creates a PageVersion in repo1 without affecting repo2."""
    repo1 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo1", local_path="/tmp/repo1")
    )
    repo2 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo2", local_path="/tmp/repo2")
    )
    await async_session.commit()

    # Initial insert for both repos
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo1.id,
            page_id="file_page:src/main.py",
            title="Repo 1 Main v1",
            content="Repo 1 v1",
            source_hash="hash1_v1",
        ),
    )
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo2.id,
            page_id="file_page:src/main.py",
            title="Repo 2 Main v1",
            content="Repo 2 v1",
            source_hash="hash2_v1",
        ),
    )
    await async_session.commit()

    # Update repo1's page
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo1.id,
            page_id="file_page:src/main.py",
            title="Repo 1 Main v2",
            content="Repo 1 v2 updated content",
            source_hash="hash1_v2",
        ),
    )
    await async_session.commit()

    # Repo1 should be at version 2 with 1 PageVersion snapshot
    p1 = await get_page(async_session, "file_page:src/main.py", repository_id=repo1.id)
    assert p1.version == 2
    assert p1.title == "Repo 1 Main v2"

    v1_list = await get_page_versions(
        async_session, "file_page:src/main.py", repository_id=repo1.id
    )
    assert len(v1_list) == 1
    assert v1_list[0].version == 1
    assert v1_list[0].repository_id == repo1.id
    assert v1_list[0].title == "Repo 1 Main v1"

    # Repo2 should remain untouched at version 1 with 0 PageVersion snapshots
    p2 = await get_page(async_session, "file_page:src/main.py", repository_id=repo2.id)
    assert p2.version == 1
    assert p2.title == "Repo 2 Main v1"

    v2_list = await get_page_versions(
        async_session, "file_page:src/main.py", repository_id=repo2.id
    )
    assert len(v2_list) == 0


async def test_upsert_pages_from_generated_batch_isolation(async_session):
    """Batch upserting generated pages isolates lookups by repository_id."""
    repo1 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo1", local_path="/tmp/repo1")
    )
    repo2 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo2", local_path="/tmp/repo2")
    )
    await async_session.commit()

    gp1 = GeneratedPage(
        page_id="file_page:src/shared.py",
        page_type="file_page",
        title="Repo 1 Shared",
        content="Shared in Repo 1",
        target_path="src/shared.py",
        source_hash="h1",
        model_name="mock",
        provider_name="mock",
    )
    gp2 = GeneratedPage(
        page_id="file_page:src/shared.py",
        page_type="file_page",
        title="Repo 2 Shared",
        content="Shared in Repo 2",
        target_path="src/shared.py",
        source_hash="h2",
        model_name="mock",
        provider_name="mock",
    )

    await upsert_pages_from_generated(async_session, [gp1], repo1.id)
    await async_session.commit()

    await upsert_pages_from_generated(async_session, [gp2], repo2.id)
    await async_session.commit()

    r1_pages = await list_pages(async_session, repo1.id)
    r2_pages = await list_pages(async_session, repo2.id)

    assert len(r1_pages) == 1
    assert r1_pages[0].title == "Repo 1 Shared"
    assert len(r2_pages) == 1
    assert r2_pages[0].title == "Repo 2 Shared"


async def test_sweep_stale_pages_isolation(async_session):
    """Sweeping stale pages in repo1 does not delete matching page IDs in repo2."""
    repo1 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo1", local_path="/tmp/repo1")
    )
    repo2 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo2", local_path="/tmp/repo2")
    )
    await async_session.commit()

    # Insert module_page in both repos
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo1.id,
            page_id="module_page:src",
            page_type="module_page",
            title="Module Repo 1",
            target_path="src",
        ),
    )
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo2.id,
            page_id="module_page:src",
            page_type="module_page",
            title="Module Repo 2",
            target_path="src",
        ),
    )
    await async_session.commit()

    # Sweep in repo1 with empty produced pages (so module_page:src is stale in repo1)
    await _sweep_stale_generated_pages(
        async_session,
        repo1.id,
        generated_pages=[],
        authoritative_page_types={"module_page"},
    )
    await async_session.commit()

    # Repo1 module page is swept
    p1 = await get_page(async_session, "module_page:src", repository_id=repo1.id)
    assert p1 is None

    # Repo2 module page survives
    p2 = await get_page(async_session, "module_page:src", repository_id=repo2.id)
    assert p2 is not None
    assert p2.repository_id == repo2.id


async def test_delete_repository_cascade_isolation(async_session):
    """Deleting repo1 cascades only repo1's pages and versions, keeping repo2 intact."""
    repo1 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo1", local_path="/tmp/repo1")
    )
    repo2 = await upsert_repository(
        async_session, **make_repo_kwargs(name="repo2", local_path="/tmp/repo2")
    )
    await async_session.commit()

    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo1.id,
            page_id="file_page:src/main.py",
            title="Repo 1 Main",
        ),
    )
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo2.id,
            page_id="file_page:src/main.py",
            title="Repo 2 Main",
        ),
    )
    await async_session.commit()

    # Delete repo1
    deleted = await delete_repository(async_session, repo1.id)
    await async_session.commit()
    assert deleted is True

    # Repo1 pages are deleted
    p1 = await get_page(async_session, "file_page:src/main.py", repository_id=repo1.id)
    assert p1 is None

    # Repo2 pages are completely intact
    p2 = await get_page(async_session, "file_page:src/main.py", repository_id=repo2.id)
    assert p2 is not None
    assert p2.repository_id == repo2.id

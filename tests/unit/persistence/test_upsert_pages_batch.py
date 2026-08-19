"""Equivalence tests for ``upsert_pages_from_generated`` (the batched end-of-run
generation persist that replaces the per-page ``upsert_page_from_generated``
loop).

The batch must be byte-for-byte equivalent to looping the single-page upsert:
- new page -> inserted at version 1
- re-persist identical content -> NO version bump, NO PageVersion snapshot
- metadata-only change (same content/hash/model) -> metadata written via the
  idempotent-touch branch, still NO version bump, NO snapshot
- content change -> PageVersion snapshot + version bump

The final test runs the batch and the per-page loop on two independent DBs over
the same mixed scenario and asserts row-for-row parity.
"""

from __future__ import annotations

from repowise.core.persistence import (
    upsert_page_from_generated,
    upsert_pages_from_generated,
)
from repowise.core.persistence.crud import get_page, get_page_versions
from tests.unit.persistence.helpers import insert_repo


def make_generated_page(page_id: str, **overrides):
    from repowise.core.generation.models import GeneratedPage

    base = dict(
        page_id=page_id,
        page_type="file_page",
        title="t",
        content=f"# {page_id}\n\nbody",
        source_hash="h0",
        model_name="mock",
        provider_name="mock",
        input_tokens=10,
        output_tokens=20,
        cached_tokens=0,
        generation_level=1,
        target_path=page_id.split(":", 1)[-1],
        confidence=1.0,
        freshness_status="fresh",
        metadata={},
        created_at="2026-07-18T00:00:00+00:00",
        updated_at="2026-07-18T00:00:00+00:00",
    )
    base.update(overrides)
    return GeneratedPage(**base)


async def test_batch_inserts_new_pages_at_version_1(async_session):
    repo = await insert_repo(async_session)
    pages = [make_generated_page(f"file_page:m{i}.py") for i in range(3)]

    result = await upsert_pages_from_generated(async_session, pages, repo.id)
    await async_session.commit()

    assert len(result) == 3
    for i in range(3):
        row = await get_page(async_session, f"file_page:m{i}.py")
        assert row is not None
        assert row.version == 1
        assert await get_page_versions(async_session, f"file_page:m{i}.py") == []


async def test_batch_idempotent_no_version_bump(async_session):
    repo = await insert_repo(async_session)
    pages = [make_generated_page("file_page:a.py")]

    await upsert_pages_from_generated(async_session, pages, repo.id)
    await async_session.commit()
    # identical re-persist (same objects, same content/hash/model)
    await upsert_pages_from_generated(async_session, pages, repo.id)
    await async_session.commit()

    row = await get_page(async_session, "file_page:a.py")
    assert row.version == 1
    assert await get_page_versions(async_session, "file_page:a.py") == []


async def test_batch_metadata_only_change_no_version_bump(async_session):
    repo = await insert_repo(async_session)
    p = make_generated_page("file_page:a.py", metadata={})

    await upsert_pages_from_generated(async_session, [p], repo.id)
    await async_session.commit()

    # metadata enrichment lands after generation; content/hash/model unchanged
    p2 = make_generated_page(
        "file_page:a.py", metadata={"wiki_links": ["x"], "backlinks": ["y"]}
    )
    await upsert_pages_from_generated(async_session, [p2], repo.id)
    await async_session.commit()

    row = await get_page(async_session, "file_page:a.py")
    assert row.version == 1  # no bump
    assert await get_page_versions(async_session, "file_page:a.py") == []  # no snapshot
    assert '"wiki_links"' in row.metadata_json  # enrichment persisted


async def test_batch_content_change_snapshots_and_bumps(async_session):
    repo = await insert_repo(async_session)
    p = make_generated_page("file_page:a.py", content="v1", source_hash="h1")

    await upsert_pages_from_generated(async_session, [p], repo.id)
    await async_session.commit()

    p2 = make_generated_page("file_page:a.py", content="v2", source_hash="h2")
    await upsert_pages_from_generated(async_session, [p2], repo.id)
    await async_session.commit()

    row = await get_page(async_session, "file_page:a.py")
    assert row.version == 2
    assert row.content == "v2"
    versions = await get_page_versions(async_session, "file_page:a.py")
    assert len(versions) == 1
    assert versions[0].content == "v1"  # prior state archived


async def test_batch_empty_is_noop(async_session):
    repo = await insert_repo(async_session)
    assert await upsert_pages_from_generated(async_session, [], repo.id) == []


async def _fresh_db():
    """A standalone in-memory DB + session factory (for the parity test)."""
    from repowise.core.persistence.database import (
        create_engine,
        create_session_factory,
        init_db,
    )

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    return engine, create_session_factory(engine)


async def test_batch_equivalent_to_per_page_loop():
    """Batch vs per-page loop on two independent DBs, mixed scenario, must be
    row-for-row identical (version, content, hash, metadata, snapshot count)."""
    from repowise.core.persistence import get_session, upsert_repository

    def scenario_round1():
        return [make_generated_page(f"file_page:m{i}.py") for i in range(5)]

    def scenario_round2():
        # m0 unchanged, m1 metadata-only, m2 content change, m3 unchanged,
        # m4 content change; plus a brand-new m5.
        return [
            make_generated_page("file_page:m0.py"),
            make_generated_page("file_page:m1.py", metadata={"wiki_links": ["z"]}),
            make_generated_page("file_page:m2.py", content="changed", source_hash="hX"),
            make_generated_page("file_page:m3.py"),
            make_generated_page("file_page:m4.py", content="changed2", source_hash="hY"),
            make_generated_page("file_page:m5.py"),
        ]

    # --- DB A: batched ---
    eng_a, sf_a = await _fresh_db()
    async with get_session(sf_a) as s:
        repo_a = await upsert_repository(s, name="a", local_path="/tmp/a")
        rid_a = repo_a.id
    async with get_session(sf_a) as s:
        await upsert_pages_from_generated(s, scenario_round1(), rid_a)
    async with get_session(sf_a) as s:
        await upsert_pages_from_generated(s, scenario_round2(), rid_a)

    # --- DB B: per-page loop ---
    eng_b, sf_b = await _fresh_db()
    async with get_session(sf_b) as s:
        repo_b = await upsert_repository(s, name="b", local_path="/tmp/b")
        rid_b = repo_b.id
    async with get_session(sf_b) as s:
        for p in scenario_round1():
            await upsert_page_from_generated(s, p, rid_b)
    async with get_session(sf_b) as s:
        for p in scenario_round2():
            await upsert_page_from_generated(s, p, rid_b)

    # --- compare ---
    ids = [f"file_page:m{i}.py" for i in range(6)]
    async with get_session(sf_a) as sa, get_session(sf_b) as sb:
        for pid in ids:
            ra = await get_page(sa, pid)
            rb = await get_page(sb, pid)
            assert (ra is None) == (rb is None), pid
            if ra is None:
                continue
            assert ra.version == rb.version, f"{pid} version"
            assert ra.content == rb.content, f"{pid} content"
            assert ra.source_hash == rb.source_hash, f"{pid} hash"
            assert ra.metadata_json == rb.metadata_json, f"{pid} metadata"
            va = await get_page_versions(sa, pid)
            vb = await get_page_versions(sb, pid)
            assert len(va) == len(vb), f"{pid} snapshot count"

    await eng_a.dispose()
    await eng_b.dispose()

"""``repowise generate`` feeds prior pages into the reuse gate (issue #1089).

Scope resolution runs over persisted page records, so a page a failed run
left as a stub IS reachable — the stub-fallback change. But unless the
generator is handed the persisted rows, the reuse gate has nothing to
compare against and every refill re-bills even unchanged pages. These tests
pin the wiring: ``execute_scoped_generation`` loads ``prior_pages`` and
passes them to ``run_generation``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)
from repowise.core.pipeline.scoped_generation import (
    ScopedGenerationResult,
    execute_scoped_generation,
)


def _page(page_id: str) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=page_id,
        page_type="module_page",
        title=f"Title {page_id}",
        content="# Body",
        source_hash="h",
        content_hash="key",
        model_name="mock-model",
        provider_name="mock",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        generation_level=4,
        target_path=page_id,
        created_at=now,
        updated_at=now,
    )


async def _insert_page(sf, repo_id: str, page: GeneratedPage) -> None:
    from repowise.core.persistence import upsert_pages_from_generated

    async with get_session(sf) as session:
        await upsert_pages_from_generated(session, [page], repo_id)


@pytest.fixture
async def engine_and_sf(tmp_path, monkeypatch):
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    db = tmp_path / ".repowise" / "wiki.db"
    url = f"sqlite+aiosqlite:///{db.as_posix()}"
    monkeypatch.setenv("REPOWISE_DB_URL", url)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    yield engine, sf
    await engine.dispose()


async def _minimal_rehydrated():
    from repowise.core.generation.page_selection import PageRecord
    from repowise.core.pipeline.scoped_generation import RehydratedRepo

    return RehydratedRepo(
        graph_builder=SimpleNamespace(),
        git_meta_map={},
        parsed_files=[],
        source_map={},
        repo_structure=SimpleNamespace(),
        records=[
            PageRecord(
                page_id="module_page:a",
                page_type="module_page",
                target_path="a",
                is_template=True,
                freshness_status="fresh",
            )
        ],
        kg_ctx=SimpleNamespace(available=False),
        deps=SimpleNamespace(),
        repo_name="demo",
    )


async def test_scoped_generation_passes_prior_pages_to_run_generation(
    engine_and_sf,
):
    """A generate run targeting a stub page still has the persisted rows
    available, so an unchanged sibling is reused instead of re-billed."""
    _engine, sf = engine_and_sf
    async with get_session(sf) as session:
        repo = await upsert_repository(session, name="demo", local_path="/tmp/demo")
        repo_id = repo.id
    await _insert_page(sf, repo_id, _page("module_page:a"))
    plan = SimpleNamespace(generate_ids={"module_page:a"}, stale_ids=set())
    seen: dict = {}

    async def fake_run_generation(*, prior_pages=None, **_kw):
        seen["prior_pages"] = prior_pages
        return [_page("module_page:a")]

    with patch(
        "repowise.core.pipeline.run_generation",
        new=fake_run_generation,
    ):
        await execute_scoped_generation(
            session_factory=sf,
            repo_id=repo_id,
            repo_path="/tmp/demo",
            rehydrated=await _minimal_rehydrated(),
            plan=plan,
            provider=SimpleNamespace(_cost_tracker=None),
            generation_config=SimpleNamespace(),
            concurrency=1,
        )

    prior = seen.get("prior_pages") or {}
    assert "module_page:a" in prior
    entry = prior["module_page:a"]
    assert entry.content_hash == "key"  # the subject key survived the round-trip
    assert entry.provider_name == "mock"  # a written row, reusable


async def test_scoped_generation_prior_load_failure_does_not_abort(engine_and_sf):
    """A broken prior-pages load degrades to no-reuse, never a failed run."""
    _engine, sf = engine_and_sf
    async with get_session(sf) as session:
        repo = await upsert_repository(session, name="demo", local_path="/tmp/demo")
        repo_id = repo.id

    plan = SimpleNamespace(generate_ids=set(), stale_ids=set())
    ran = False

    async def fake_run_generation(*, prior_pages=None, **_kw):
        nonlocal ran
        ran = True
        assert prior_pages == {}
        return []

    with patch(
        "repowise.core.pipeline.run_generation",
        new=fake_run_generation,
    ), patch(
        "repowise.core.persistence.load_prior_pages",
        side_effect=RuntimeError("db down"),
    ):
        result = await execute_scoped_generation(
            session_factory=sf,
            repo_id=repo_id,
            repo_path="/tmp/demo",
            rehydrated=await _minimal_rehydrated(),
            plan=plan,
            provider=SimpleNamespace(_cost_tracker=None),
            generation_config=SimpleNamespace(),
            concurrency=1,
        )

    assert ran
    assert isinstance(result, ScopedGenerationResult)

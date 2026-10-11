"""The GenerationJob row must not count a failure stub as a written page.

The web UI reads this row, not the job checkpoint under ``.repowise/jobs/``.
Before the stub fallback (issue #1089) a failed page contributed nothing to
``generated_pages``, so the row under-counted. Now it has a row, and counting
it as completed would let a run that lost half its pages report a clean sweep.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from repowise.cli._repo_session import open_repo_db
from repowise.cli.commands.init_cmd.persistence import persist_result
from repowise.core.generation.models import STUB_FALLBACK_ERROR, GeneratedPage
from repowise.core.persistence import get_session
from repowise.core.persistence.models import GenerationJob


def _page(target: str, *, stub_error: str | None = None) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    page = GeneratedPage(
        page_id=f"module_page:{target}",
        page_type="module_page",
        title=target,
        content=f"content for {target}",
        source_hash="x" * 64,
        model_name="mock",
        provider_name="template" if stub_error else "mock",
        input_tokens=0 if stub_error else 1,
        output_tokens=0 if stub_error else 1,
        cached_tokens=0,
        generation_level=4,
        target_path=target,
        created_at=now,
        updated_at=now,
    )
    if stub_error:
        page.metadata[STUB_FALLBACK_ERROR] = stub_error
    return page


def _result(pages: list[GeneratedPage]) -> SimpleNamespace:
    return SimpleNamespace(
        repo_name="r",
        index_persisted_incrementally=True,
        generated_pages=pages,
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


async def _job(repo_path) -> GenerationJob:
    engine, sf, _ = await open_repo_db(repo_path, repo_name="r")
    try:
        async with get_session(sf) as session:
            return (await session.execute(select(GenerationJob))).scalars().one()
    finally:
        await engine.dispose()


async def test_stub_fallbacks_are_counted_as_failed_not_completed(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    pages = [
        _page("a"),
        _page("b"),
        _page("c", stub_error="upstream 529 overloaded"),
    ]
    await persist_result(_result(pages), repo_path)

    job = await _job(repo_path)
    assert job.total_pages == 3
    assert job.completed_pages == 2
    assert job.failed_pages == 1


async def test_a_clean_run_still_reports_every_page_completed(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    await persist_result(_result([_page("a"), _page("b")]), repo_path)

    job = await _job(repo_path)
    assert job.total_pages == 2
    assert job.completed_pages == 2
    assert job.failed_pages == 0

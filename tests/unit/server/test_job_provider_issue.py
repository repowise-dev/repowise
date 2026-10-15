"""A job that went wrong with its provider says so on the job record.

Per-page provider errors leave stubs and the job still completes, and a
provider that cannot be built skips docs; both used to read as a clean run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from repowise.core.generation.models import (
    STUB_FALLBACK_ERROR,
    STUB_FALLBACK_HINT,
    GeneratedPage,
)
from repowise.core.persistence.crud import (
    get_generation_job,
    upsert_generation_job,
    upsert_repository,
)
from repowise.core.providers.llm.base import ProviderError
from repowise.server.job_executor import execute_job

_AUTH = {
    "kind": "auth",
    "message": "API key rejected by openai. Update it in Settings.",
    "provider": "openai",
}


def _page(target: str, hint: dict | None = None) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    page = GeneratedPage(
        page_id=f"module_page:{target}",
        page_type="module_page",
        title=target,
        content="body",
        source_hash="x" * 64,
        model_name="gpt-x",
        provider_name="openai",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=4,
        target_path=target,
        created_at=now,
        updated_at=now,
    )
    if hint is not None:
        page.metadata[STUB_FALLBACK_ERROR] = "Error code: 401"
        page.metadata[STUB_FALLBACK_HINT] = hint
    return page


def _result(pages: list) -> SimpleNamespace:
    return SimpleNamespace(generated_pages=pages, parsed_files=[], file_count=1, symbol_count=2)


async def _seed(session_factory, repo_path, mode: str) -> str:
    async with session_factory() as session:
        repo = await upsert_repository(session, name="r", local_path=str(repo_path), settings={})
        job = await upsert_generation_job(session, repository_id=repo.id, config={"mode": mode})
        await session.commit()
        return job.id


async def _run(session_factory, job_id, *, pipeline, provider):
    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)
    provider_patch = (
        patch("repowise.server.provider_config.get_chat_provider_instance", return_value=provider)
        if provider is not None
        else patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            side_effect=RuntimeError("No active provider configured."),
        )
    )
    with (
        patch("repowise.server.job_executor.run_pipeline", pipeline),
        patch("repowise.server.job_executor.persist_pipeline_result", AsyncMock()),
        patch("repowise.server.job_executor._persist_initial_index_state"),
        provider_patch,
    ):
        await execute_job(job_id, app_state)
    async with session_factory() as session:
        job = await get_generation_job(session, job_id)
        return job, json.loads(job.config_json)


_PROVIDER = SimpleNamespace(provider_name="openai", model_name="gpt-x")


@pytest.mark.asyncio
async def test_pages_lost_to_a_rejected_key_complete_with_the_hint(session_factory, tmp_path):
    job_id = await _seed(session_factory, tmp_path, "full_resync")
    pages = [_page("a", _AUTH), _page("b", _AUTH)]

    job, config = await _run(
        session_factory, job_id, pipeline=AsyncMock(return_value=_result(pages)), provider=_PROVIDER
    )

    assert job.status == "completed"
    assert (job.completed_pages, job.failed_pages) == (0, 2)
    assert config["provider_issue"] == _AUTH


@pytest.mark.asyncio
async def test_a_clean_run_records_no_issue(session_factory, tmp_path):
    job_id = await _seed(session_factory, tmp_path, "full_resync")

    _, config = await _run(
        session_factory,
        job_id,
        pipeline=AsyncMock(return_value=_result([_page("a")])),
        provider=_PROVIDER,
    )

    assert config["provider_issue"] is None


@pytest.mark.parametrize("mode", ["sync", "full_resync"])
@pytest.mark.asyncio
async def test_a_configured_provider_that_will_not_load_is_reported(
    session_factory, tmp_path, mode
):
    job_id = await _seed(session_factory, tmp_path, mode)

    with patch(
        "repowise.server.provider_config.get_active_provider", return_value=("openai", None)
    ):
        _, config = await _run(
            session_factory, job_id, pipeline=AsyncMock(return_value=_result([])), provider=None
        )

    assert config["provider_issue"]["kind"] == "docs_skipped"
    # Fixed copy, never the raw load error.
    assert "No active provider" not in config["provider_issue"]["message"]


@pytest.mark.parametrize("mode", ["sync", "full_resync"])
@pytest.mark.asyncio
async def test_a_deliberately_keyless_repo_is_not_a_problem(session_factory, tmp_path, mode):
    job_id = await _seed(session_factory, tmp_path, mode)

    with patch("repowise.server.provider_config.get_active_provider", return_value=(None, None)):
        _, config = await _run(
            session_factory, job_id, pipeline=AsyncMock(return_value=_result([])), provider=None
        )

    assert config["provider_issue"] is None


@pytest.mark.asyncio
async def test_a_keyless_first_index_is_not_a_problem(session_factory, tmp_path):
    job_id = await _seed(session_factory, tmp_path, "initial_index")

    _, config = await _run(
        session_factory, job_id, pipeline=AsyncMock(return_value=_result([])), provider=None
    )

    assert config["provider_issue"] is None


@pytest.mark.asyncio
async def test_a_job_failing_on_the_provider_records_the_hint_not_the_raw_error(
    session_factory, tmp_path
):
    job_id = await _seed(session_factory, tmp_path, "full_resync")
    pipeline = AsyncMock(side_effect=ProviderError("openai", "Incorrect API key sk-live", 401))

    job, config = await _run(session_factory, job_id, pipeline=pipeline, provider=_PROVIDER)

    assert job.status == "failed"
    assert job.error_message == _AUTH["message"]
    assert config["provider_issue"] == _AUTH
    assert config["mode"] == "full_resync"

"""Tests for the background job executor's exclude-pattern handling.

Server-triggered jobs (web sync, full-resync, workspace sync, webhooks,
scheduler) all route through ``execute_job``. These tests prove that the
repository's ``exclude_patterns`` reach ``run_pipeline`` so excluded paths
are not re-indexed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from repowise.core.persistence.crud import upsert_generation_job, upsert_repository
from repowise.server.job_executor import _repo_exclude_patterns, execute_job


def _fake_result() -> SimpleNamespace:
    """Minimal stand-in for a PipelineResult (persist is mocked out)."""
    return SimpleNamespace(
        generated_pages=[],
        parsed_files=[],
        file_count=1,
        symbol_count=2,
    )


async def _seed_repo_and_job(
    session_factory,
    repo_path,
    *,
    settings: dict | None = None,
) -> str:
    """Insert a repo (with settings) + a pending full_resync job; return job_id."""
    async with session_factory() as session:
        repo = await upsert_repository(
            session,
            name="test-repo",
            local_path=str(repo_path),
            settings=settings or {},
        )
        job = await upsert_generation_job(
            session,
            repository_id=repo.id,
            config={"mode": "full_resync"},
        )
        await session.commit()
        return job.id


@pytest.mark.asyncio
async def test_execute_job_passes_exclude_patterns_from_settings(session_factory, tmp_path):
    """settings_json exclude_patterns must reach run_pipeline."""
    job_id = await _seed_repo_and_job(
        session_factory,
        tmp_path,
        settings={"exclude_patterns": ["tools/", "node_modules/"]},
    )

    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)

    run_pipeline_mock = AsyncMock(return_value=_fake_result())
    with (
        patch("repowise.server.job_executor.run_pipeline", run_pipeline_mock),
        patch("repowise.server.job_executor.persist_pipeline_result", AsyncMock()),
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            side_effect=RuntimeError("no provider"),
        ),
    ):
        await execute_job(job_id, app_state)

    run_pipeline_mock.assert_awaited_once()
    assert run_pipeline_mock.await_args.kwargs["exclude_patterns"] == [
        "tools/",
        "node_modules/",
    ]


@pytest.mark.asyncio
async def test_execute_job_no_excludes_passes_none(session_factory, tmp_path):
    """With no configured excludes, run_pipeline receives None (not [])."""
    job_id = await _seed_repo_and_job(session_factory, tmp_path, settings={})

    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)

    run_pipeline_mock = AsyncMock(return_value=_fake_result())
    with (
        patch("repowise.server.job_executor.run_pipeline", run_pipeline_mock),
        patch("repowise.server.job_executor.persist_pipeline_result", AsyncMock()),
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            side_effect=RuntimeError("no provider"),
        ),
    ):
        await execute_job(job_id, app_state)

    run_pipeline_mock.assert_awaited_once()
    assert run_pipeline_mock.await_args.kwargs["exclude_patterns"] is None


def test_repo_exclude_patterns_merges_settings_and_config(tmp_path):
    """DB settings + .repowise/config.yaml merge, order-preserved & de-duped."""
    import json

    config_dir = tmp_path / ".repowise"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "exclude_patterns:\n  - node_modules/\n  - vendor/\n", encoding="utf-8"
    )

    repo = SimpleNamespace(
        settings_json=json.dumps({"exclude_patterns": ["tools/", "node_modules/"]})
    )

    patterns = _repo_exclude_patterns(repo, str(tmp_path))

    assert patterns == ["tools/", "node_modules/", "vendor/"]


def test_repo_exclude_patterns_ignores_malformed_sources(tmp_path):
    """Malformed settings_json / missing config are ignored, not fatal."""
    repo = SimpleNamespace(settings_json="{not valid json")
    assert _repo_exclude_patterns(repo, str(tmp_path)) == []


@pytest.mark.asyncio
async def test_execute_job_merges_config_yaml_excludes(session_factory, tmp_path):
    """End-to-end regression: the real user case (tools/ excluded).

    Repo settings carry ``exclude_patterns: ["tools/"]``; the job path must
    forward exactly that to run_pipeline so ``tools/`` is never re-indexed.
    """
    job_id = await _seed_repo_and_job(
        session_factory,
        tmp_path,
        settings={"exclude_patterns": ["tools/"]},
    )

    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)

    run_pipeline_mock = AsyncMock(return_value=_fake_result())
    with (
        patch("repowise.server.job_executor.run_pipeline", run_pipeline_mock),
        patch("repowise.server.job_executor.persist_pipeline_result", AsyncMock()),
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            side_effect=RuntimeError("no provider"),
        ),
    ):
        await execute_job(job_id, app_state)

    run_pipeline_mock.assert_awaited_once()
    assert run_pipeline_mock.await_args.kwargs["exclude_patterns"] == ["tools/"]

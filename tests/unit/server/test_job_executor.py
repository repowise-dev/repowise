"""Tests for the background job executor's exclude-pattern handling.

Server-triggered jobs (web sync, full-resync, workspace sync, webhooks,
scheduler) all route through ``execute_job``. These tests prove that the
repository's ``exclude_patterns`` reach ``run_pipeline`` so excluded paths
are not re-indexed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.persistence.crud import upsert_generation_job, upsert_repository
from repowise.server.job_executor import (
    _incremental_page_regen,
    _repo_exclude_patterns,
    execute_job,
)


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


@pytest.mark.asyncio
async def test_incremental_page_regen_passes_repo_path(tmp_path):
    """Incremental regen must forward repo_path to generate_all.

    Without it, generate_all can't load the curated knowledge-graph.json
    artifact and silently falls back to community module grouping, which
    overwrites curated module pages with the wrong ids.
    """
    repo_path = tmp_path
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "state.json").write_text(
        '{"last_sync_commit": "base-sha"}', encoding="utf-8"
    )

    result = SimpleNamespace(
        file_count=10,
        parsed_files=[],
        source_map={},
        graph_builder=SimpleNamespace(graph=lambda: object()),
        repo_structure=object(),
        repo_name="test-repo",
        git_meta_map={},
    )

    # Stub git HEAD lookup to a sha != base so regen proceeds.
    head_proc = SimpleNamespace(returncode=0, stdout="head-sha\n")

    # Detector reports one changed/affected file so generation runs.
    detector = MagicMock()
    detector.get_changed_files.return_value = [object()]
    affected = SimpleNamespace(regenerate=["foo.py"])
    detector.get_affected_pages.return_value = affected

    generator = MagicMock()
    generator.generate_all = AsyncMock(return_value=[])

    with (
        patch("subprocess.run", return_value=head_proc),
        patch(
            "repowise.core.ingestion.ChangeDetector",
            return_value=detector,
        ),
        patch(
            "repowise.core.ingestion.change_detector.compute_adaptive_budget",
            return_value=5,
        ),
        patch("repowise.core.generation.PageGenerator", return_value=generator),
        patch("repowise.core.generation.ContextAssembler"),
        patch("repowise.core.generation.GenerationConfig"),
        patch("repowise.core.reasoning.resolve_reasoning", return_value="low"),
        patch("repowise.core.repo_config.load_repo_config", return_value={}),
    ):
        await _incremental_page_regen(
            Path(repo_path),
            result,
            llm_client=object(),
            job_config={},
            progress=None,
        )

    generator.generate_all.assert_awaited_once()
    assert generator.generate_all.await_args.kwargs["repo_path"] == Path(repo_path)

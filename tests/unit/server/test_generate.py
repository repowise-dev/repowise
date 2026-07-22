"""Tests for the HTTP scoped-generation surface.

Covers the ``/api/repos/{id}/generate`` + ``/generate/estimate`` endpoints, the
``generate`` job mode in the executor (the shared core engine), the D1 launch fix
on the regenerate endpoint, and the D3 wiring on the incremental sync path.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from repowise.core.generation.cascade import build_page_dependencies
from repowise.core.generation.page_selection import PageRecord, PageSelectionIntent
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.pipeline.scoped_generation import RehydratedRepo, ScopedGenerationResult
from repowise.server.job_executor import _build_generate_intent
from tests.unit.server.conftest import create_test_repo

# ---------------------------------------------------------------------------
# _build_generate_intent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"selection": {"kind": "all"}}, PageSelectionIntent(all_pages=True)),
        ({"selection": {"kind": "unwritten"}}, PageSelectionIntent(unwritten=True)),
        ({"selection": {"kind": "stale"}}, PageSelectionIntent(stale=True)),
        (
            {"selection": {"kind": "page_ids", "page_ids": ["file_page:a.py"]}},
            PageSelectionIntent(page_ids=("file_page:a.py",)),
        ),
        (
            {"selection": {"kind": "path_prefix", "path_prefix": "src/"}},
            PageSelectionIntent(path_globs=("src/",)),
        ),
        ({}, PageSelectionIntent(unwritten=True)),
        (
            {"mode": "single_page", "page_id": "file_page:a.py"},
            PageSelectionIntent(page_ids=("file_page:a.py",)),
        ),
    ],
)
def test_build_generate_intent(config: dict, expected: PageSelectionIntent) -> None:
    assert _build_generate_intent(config) == expected


# ---------------------------------------------------------------------------
# POST /generate + /generate/estimate endpoints
# ---------------------------------------------------------------------------


async def _job_config(session_factory, job_id: str) -> dict:
    async with get_session(session_factory) as session:
        job = await crud.get_generation_job(session, job_id)
        return json.loads(job.config_json) if job.config_json else {}


@pytest.mark.asyncio
async def test_generate_endpoint_creates_and_launches_job(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    with patch("repowise.server.routers.repos._launch_job_task") as launch:
        resp = await client.post(
            f"/api/repos/{repo['id']}/generate",
            json={"selection": {"kind": "unwritten"}, "cascade": "dependents"},
        )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    launch.assert_called_once()

    cfg = await _job_config(app.state.session_factory, job_id)
    assert cfg["mode"] == "generate"
    assert cfg["selection"] == {"kind": "unwritten"}
    assert cfg["cascade"] == "dependents"


@pytest.mark.asyncio
async def test_generate_endpoint_page_ids_selection(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    with patch("repowise.server.routers.repos._launch_job_task"):
        resp = await client.post(
            f"/api/repos/{repo['id']}/generate",
            json={
                "selection": {"kind": "page_ids", "page_ids": ["file_page:a.py"]},
                "cascade": "none",
                "style": "caveman",
            },
        )
    assert resp.status_code == 202
    cfg = await _job_config(app.state.session_factory, resp.json()["job_id"])
    assert cfg["selection"] == {"kind": "page_ids", "page_ids": ["file_page:a.py"]}
    assert cfg["style"] == "caveman"


@pytest.mark.asyncio
async def test_generate_endpoint_rejects_unknown_style(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.post(
        f"/api/repos/{repo['id']}/generate",
        json={"selection": {"kind": "all"}, "style": "bogus"},
    )
    assert resp.status_code == 400
    assert "style" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_endpoint_404_unknown_repo(client: AsyncClient) -> None:
    resp = await client.post("/api/repos/nope/generate", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_endpoint_409_when_job_active(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_generation_job(
            session, repository_id=repo["id"], status="running", config={"mode": "sync"}
        )
        await session.commit()
    resp = await client.post(f"/api/repos/{repo['id']}/generate", json={})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_generate_estimate_no_pages(client: AsyncClient) -> None:
    """A repo with no wiki pages returns a zero estimate, not a crash."""
    repo = await create_test_repo(client)
    with patch(
        "repowise.server.provider_config.get_chat_provider_instance",
        side_effect=RuntimeError("no provider"),
    ):
        resp = await client.post(
            f"/api/repos/{repo['id']}/generate/estimate",
            json={"selection": {"kind": "unwritten"}},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_pages"] == 0
    assert body["estimate"] is None
    assert body["provider"]["error"] is not None


# ---------------------------------------------------------------------------
# generate job execution (shared core engine)
# ---------------------------------------------------------------------------


def _fake_rehydrated(template_ids: list[str]) -> RehydratedRepo:
    records = [
        PageRecord(
            page_id=pid,
            page_type=pid.split(":", 1)[0],
            target_path=pid.split(":", 1)[1],
            is_template=True,
            freshness_status="fresh",
        )
        for pid in template_ids
    ]
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=[]
    )
    return RehydratedRepo(
        graph_builder=MagicMock(),
        git_meta_map={},
        parsed_files=[],
        source_map={},
        repo_structure=MagicMock(),
        records=records,
        kg_ctx=MagicMock(available=False),
        deps=deps,
        repo_name="test-repo",
    )


async def _seed_generate_job(session_factory, repo_path: Path, config: dict) -> str:
    async with session_factory() as session:
        repo = await crud.upsert_repository(
            session, name="test-repo", local_path=str(repo_path)
        )
        job = await crud.upsert_generation_job(
            session, repository_id=repo.id, config=config
        )
        await session.commit()
        return job.id


@pytest.mark.asyncio
async def test_generate_job_runs_scoped_engine(session_factory, tmp_path) -> None:
    """A generate job resolves the scope and drives the shared core engine."""
    from repowise.server.job_executor import execute_job

    (tmp_path / ".git").mkdir()
    job_id = await _seed_generate_job(
        session_factory,
        tmp_path,
        {"mode": "generate", "selection": {"kind": "unwritten"}, "cascade": "none"},
    )
    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)

    template_ids = ["file_page:a.py", "file_page:b.py"]
    written = SimpleNamespace(
        page_id="file_page:a.py", title="a", content="x", input_tokens=10, output_tokens=20
    )
    execute_mock = AsyncMock(
        return_value=ScopedGenerationResult(generated_pages=[written], marked_stale=0)
    )
    with (
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            return_value=MagicMock(provider_name="openai", model_name="gpt"),
        ),
        patch(
            "repowise.server.search_helpers.resolve_repo_vector_store",
            AsyncMock(return_value=None),
        ),
        patch(
            "repowise.core.pipeline.scoped_generation.rehydrate_repo",
            AsyncMock(return_value=_fake_rehydrated(template_ids)),
        ),
        patch(
            "repowise.core.pipeline.scoped_generation.execute_scoped_generation",
            execute_mock,
        ),
    ):
        await execute_job(job_id, app_state)

    execute_mock.assert_awaited_once()
    plan = execute_mock.await_args.kwargs["plan"]
    assert plan.generate_ids == set(template_ids)

    async with get_session(session_factory) as session:
        job = await crud.get_generation_job(session, job_id)
        assert job.status == "completed"
        cfg = json.loads(job.config_json)
        assert cfg["pages_generated"] == 1


@pytest.mark.asyncio
async def test_generate_job_fails_without_provider(session_factory, tmp_path) -> None:
    from repowise.server.job_executor import execute_job

    (tmp_path / ".git").mkdir()
    job_id = await _seed_generate_job(
        session_factory, tmp_path, {"mode": "generate", "selection": {"kind": "all"}}
    )
    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)

    with (
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            side_effect=RuntimeError("no provider"),
        ),
        patch(
            "repowise.server.search_helpers.resolve_repo_vector_store",
            AsyncMock(return_value=None),
        ),
    ):
        await execute_job(job_id, app_state)

    async with get_session(session_factory) as session:
        job = await crud.get_generation_job(session, job_id)
        assert job.status == "failed"


# ---------------------------------------------------------------------------
# D3: incremental sync hands the regen a vector store + prior pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_regen_receives_vector_store_and_prior_pages(session_factory, tmp_path) -> None:
    from repowise.server.job_executor import execute_job

    (tmp_path / ".git").mkdir()
    job_id = await _seed_generate_job(session_factory, tmp_path, {"mode": "sync"})
    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)

    fake_result = SimpleNamespace(
        generated_pages=[], parsed_files=[], file_count=1, symbol_count=1
    )
    regen_mock = AsyncMock(return_value=[])
    with (
        patch("repowise.server.job_executor.run_pipeline", AsyncMock(return_value=fake_result)),
        patch("repowise.server.job_executor.persist_pipeline_result", AsyncMock(return_value=[])),
        patch("repowise.server.job_executor._incremental_page_regen", regen_mock),
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            return_value=MagicMock(provider_name="openai", model_name="gpt"),
        ),
        patch(
            "repowise.server.search_helpers.resolve_repo_vector_store",
            AsyncMock(return_value=None),
        ),
    ):
        await execute_job(job_id, app_state)

    regen_mock.assert_awaited_once()
    assert "vector_store" in regen_mock.await_args.kwargs
    assert "prior_pages" in regen_mock.await_args.kwargs

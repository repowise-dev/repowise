"""Tests for API-driven first-time indexing.

Covers repo-local database registration on create, the explicit
``POST /api/repos/{id}/index`` trigger, the pre-flight check, real job
cancellation, the ``initial_index`` executor mode's state.json baseline,
and the job event buffer that feeds the SSE message stream.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from repowise.core.cancellation import PipelineCancelled
from repowise.core.persistence.crud import (
    get_generation_job,
    upsert_generation_job,
    upsert_repository,
)
from repowise.server.job_events import JobEventBuffer, create_event_buffer
from repowise.server.job_executor import execute_job


def _make_fake_repo(tmp_path: Path, name: str = "fresh-repo") -> Path:
    repo_dir = tmp_path / name
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    (repo_dir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (repo_dir / "util.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return repo_dir


async def _noop_execute(job_id, app_state, session_factory_override=None):
    return None


# ---------------------------------------------------------------------------
# Repo-local DB registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_index_uses_repo_local_db(
    client: AsyncClient, app, tmp_path, monkeypatch
) -> None:
    """POST /api/repos with index=true stores the repo in its own wiki.db."""
    from repowise.server.routers import repos as repos_module

    monkeypatch.setattr(repos_module, "execute_job", _noop_execute)
    repo_dir = _make_fake_repo(tmp_path)

    resp = await client.post(
        "/api/repos",
        json={"name": "fresh-repo", "local_path": str(repo_dir), "index": True},
    )
    assert resp.status_code == 201
    data = resp.json()
    repo_id = data["id"]
    assert data["initial_job_id"]

    # The canonical store is the repo-local wiki.db, with the job row inside.
    db_file = repo_dir / ".repowise" / "wiki.db"
    assert db_file.is_file()
    with sqlite3.connect(str(db_file)) as conn:
        row = conn.execute("SELECT id FROM repositories LIMIT 1").fetchone()
        assert row[0] == repo_id
        job_row = conn.execute("SELECT config_json FROM generation_jobs LIMIT 1").fetchone()
        assert json.loads(job_row[0])["mode"] == "initial_index"

    # Routed reads resolve through the registered per-repo factory.
    assert repo_id in app.state.workspace_sessions
    get_resp = await client.get(f"/api/repos/{repo_id}")
    assert get_resp.status_code == 200

    # The primary DB carries a registry row with the same id, and the repo
    # appears exactly once in listings.
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository

    async with app.state.session_factory() as session:
        result = await session.execute(select(Repository.id))
        assert [r[0] for r in result.all()] == [repo_id]

    list_resp = await client.get("/api/repos")
    assert [r["id"] for r in list_resp.json()] == [repo_id]


@pytest.mark.asyncio
async def test_index_endpoint_registers_and_enqueues(
    client: AsyncClient, app, tmp_path, monkeypatch
) -> None:
    """POST /{id}/index takes a metadata-only repo to a repo-local index job."""
    from repowise.server.routers import repos as repos_module

    monkeypatch.setattr(repos_module, "execute_job", _noop_execute)
    repo_dir = _make_fake_repo(tmp_path)

    created = await client.post(
        "/api/repos",
        json={"name": "fresh-repo", "local_path": str(repo_dir), "index": False},
    )
    repo_id = created.json()["id"]
    assert not (repo_dir / ".repowise" / "wiki.db").exists()

    resp = await client.post(f"/api/repos/{repo_id}/index")
    assert resp.status_code == 202
    assert resp.json()["job_id"]
    assert (repo_dir / ".repowise" / "wiki.db").is_file()
    assert repo_id in app.state.workspace_sessions

    # Active-job guard: a second trigger is rejected while the first pends.
    resp2 = await client.post(f"/api/repos/{repo_id}/index")
    assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_reports_provider_and_estimate(
    client: AsyncClient, tmp_path, monkeypatch
) -> None:
    repo_dir = _make_fake_repo(tmp_path)
    created = await client.post(
        "/api/repos",
        json={"name": "fresh-repo", "local_path": str(repo_dir), "index": False},
    )
    repo_id = created.json()["id"]

    fake_provider = SimpleNamespace(
        provider_name="mock",
        model_name="claude-sonnet-4-6",
        generate=AsyncMock(return_value="OK"),
    )
    with patch(
        "repowise.server.provider_config.get_chat_provider_instance",
        return_value=fake_provider,
    ):
        resp = await client.post(f"/api/repos/{repo_id}/preflight")

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == {
        "ok": True,
        "name": "mock",
        "model": "claude-sonnet-4-6",
        "error": None,
    }
    assert data["file_count"] >= 2
    assert data["estimate"]["total_pages"] >= 1
    assert data["estimate"]["estimated_cost_usd"] > 0


@pytest.mark.asyncio
async def test_preflight_surfaces_provider_failure(client: AsyncClient, tmp_path) -> None:
    repo_dir = _make_fake_repo(tmp_path)
    created = await client.post(
        "/api/repos",
        json={"name": "fresh-repo", "local_path": str(repo_dir), "index": False},
    )
    repo_id = created.json()["id"]

    with patch(
        "repowise.server.provider_config.get_chat_provider_instance",
        side_effect=RuntimeError("No active provider configured"),
    ):
        resp = await client.post(f"/api/repos/{repo_id}/preflight")

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"]["ok"] is False
    assert "No active provider" in data["provider"]["error"]
    assert data["estimate"] is None


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_stops_task_and_marks_cancelled(
    client: AsyncClient, app, session_factory, tmp_path
) -> None:
    """POST /cancel flips the token, cancels the task, and records 'cancelled'."""
    async with session_factory() as session:
        repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
        job = await upsert_generation_job(session, repository_id=repo.id, status="running")
        await session.commit()
        job_id = job.id

    from repowise.core.cancellation import CancellationToken

    token = CancellationToken()
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    app.state.job_cancel_tokens = {job_id: token}
    app.state.job_tasks = {job_id: task}

    resp = await client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert token.cancelled

    with pytest.raises(asyncio.CancelledError):
        await task

    # Terminal state: a second cancel is a 409.
    resp2 = await client.post(f"/api/jobs/{job_id}/cancel")
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_execute_job_records_cancelled_status(session_factory, tmp_path) -> None:
    """A pipeline unwound by PipelineCancelled ends as 'cancelled', not 'failed'."""
    async with session_factory() as session:
        repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
        job = await upsert_generation_job(
            session, repository_id=repo.id, config={"mode": "full_resync"}
        )
        await session.commit()
        job_id = job.id

    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)

    with (
        patch(
            "repowise.server.job_executor.run_pipeline",
            AsyncMock(side_effect=PipelineCancelled()),
        ),
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            side_effect=RuntimeError("no provider"),
        ),
    ):
        await execute_job(job_id, app_state)

    async with session_factory() as session:
        refreshed = await get_generation_job(session, job_id)
        assert refreshed.status == "cancelled"
        assert refreshed.finished_at is not None
    assert app_state.job_cancel_tokens == {}


# ---------------------------------------------------------------------------
# initial_index executor mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_job_initial_index_writes_state_baseline(session_factory, tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    async with session_factory() as session:
        repo = await upsert_repository(session, name="r", local_path=str(repo_dir))
        job = await upsert_generation_job(
            session, repository_id=repo.id, config={"mode": "initial_index"}
        )
        await session.commit()
        job_id = job.id

    app_state = SimpleNamespace(session_factory=session_factory, fts=None, vector_store=None)
    result = SimpleNamespace(generated_pages=[], parsed_files=[], file_count=2, symbol_count=3)

    with (
        patch("repowise.server.job_executor.run_pipeline", AsyncMock(return_value=result)),
        patch("repowise.server.job_executor.persist_pipeline_result", AsyncMock()),
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            side_effect=RuntimeError("no provider"),
        ),
    ):
        await execute_job(job_id, app_state)

    async with session_factory() as session:
        refreshed = await get_generation_job(session, job_id)
        assert refreshed.status == "completed"

    state = json.loads((repo_dir / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["docs_enabled"] is False
    assert "no provider configured" in state["docs_skip_reason"]
    assert state["run_mode"] == "standard"
    assert state["git_tier"] == "full"
    assert state["config_fingerprint"]
    assert state["store_format_version"]

    # The no-provider warning reached the event buffer for the SSE stream.
    events = app_state.job_events[job_id].since(0)
    assert any("No LLM provider configured" in e["text"] for e in events)


# ---------------------------------------------------------------------------
# Job event buffer
# ---------------------------------------------------------------------------


def test_job_event_buffer_sequences_and_phases() -> None:
    buf = JobEventBuffer()
    buf.set_phase("parse", 100)
    buf.add("warning", "1 file skipped")
    buf.set_phase("generation", None)

    events = buf.since(0)
    assert [e["seq"] for e in events] == [0, 1, 2]
    assert events[0]["text"] == "Phase started: parse (100 items)"
    assert events[1]["level"] == "warning"
    assert events[1]["phase"] == "parse"
    assert events[2]["phase"] == "generation"
    assert buf.phase == "generation"

    # Incremental drain: only new events come back.
    assert [e["seq"] for e in buf.since(2)] == [2]


def test_event_buffer_registry_evicts_oldest() -> None:
    state = SimpleNamespace()
    for i in range(55):
        create_event_buffer(state, f"job-{i}")
    assert len(state.job_events) == 50
    assert "job-0" not in state.job_events
    assert "job-54" in state.job_events

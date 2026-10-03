"""/api/repos branches the per-feature tests leave unexercised.

Pins current answers on the easily broken paths: workspace fan-out, state.json
fallbacks, one-sided freshness, job-launch failures, estimate and preflight
edges, and the file-content guards.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session, init_db
from repowise.core.persistence.models import GraphNode, HealthFileMetric, Repository
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.server.routers import repos as repos_module
from tests.unit.server.conftest import create_test_repo


async def _noop_execute(job_id, app_state, session_factory_override=None):
    return None


def _git_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    return root


async def _second_db(local_path: Path, *, indexed: bool = True):
    """A separate in-memory database holding one repository, like a repo wiki.db."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with get_session(factory) as session:
        repo = await crud.upsert_repository(
            session, name=local_path.name, local_path=str(local_path)
        )
        if indexed:
            session.add(GraphNode(repository_id=repo.id, node_id="a.py", node_type="file"))
        repo_id = repo.id
    return engine, factory, repo_id


def _broken_factory():
    raise RuntimeError("store unreadable")


def _workspace(app, ws_root: Path, entries: list[RepoEntry]) -> None:
    app.state.workspace_config = WorkspaceConfig(
        version=1, repos=entries, default_repo=entries[0].alias
    )
    app.state.workspace_root = str(ws_root)


# ---------------------------------------------------------------------------
# GET /api/repos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_merges_workspace_databases_and_skips_unreadable_ones(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    primary = await create_test_repo(client, tmp_path)
    engine, factory, other_id = await _second_db(_git_dir(tmp_path / "other"))
    _e2, empty_factory, _ = await _second_db(_git_dir(tmp_path / "empty"))
    app.state.workspace_sessions = {
        primary["id"]: _broken_factory,  # already listed: never opened
        other_id: factory,
        "not-in-that-db": empty_factory,
        "broken": _broken_factory,
    }
    try:
        resp = await client.get("/api/repos")
    finally:
        await engine.dispose()
        await _e2.dispose()

    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}
    assert set(rows) == {primary["id"], other_id}
    # The workspace row has file nodes in its own DB, so it counts as indexed.
    assert rows[other_id]["workspace_status"] is None
    assert rows[primary["id"]]["workspace_status"] == "needs_index"


@pytest.mark.asyncio
async def test_list_status_probe_oserror_reads_as_needs_index(
    client: AsyncClient, tmp_path: Path, monkeypatch
) -> None:
    repo = await create_test_repo(client, tmp_path)
    target = repo["local_path"]
    real_is_dir = Path.is_dir

    def flaky_is_dir(self: Path) -> bool:
        if str(self) == target:
            raise OSError("permission denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", flaky_is_dir)
    resp = await client.get("/api/repos")

    assert resp.json()[0]["workspace_status"] == "needs_index"


@pytest.mark.asyncio
async def test_list_workspace_attach_reads_state_file_per_repo(
    client: AsyncClient, app, session, tmp_path: Path
) -> None:
    ws_root = tmp_path / "ws"
    legacy = _git_dir(ws_root / "legacy")
    (legacy / ".repowise").mkdir()
    (legacy / ".repowise" / "state.json").write_text(
        json.dumps({"run_mode": "fast", "git_tier": "essential"}), encoding="utf-8"
    )
    broken = _git_dir(ws_root / "broken")
    (broken / ".repowise").mkdir()
    (broken / ".repowise" / "state.json").write_text("{not json", encoding="utf-8")
    outside = _git_dir(tmp_path / "outside")

    legacy_row = await crud.upsert_repository(session, name="legacy", local_path=str(legacy))
    broken_row = await crud.upsert_repository(session, name="broken", local_path=str(broken))
    outside_row = await crud.upsert_repository(session, name="outside", local_path=str(outside))
    no_path_row = await crud.upsert_repository(session, name="nopath", local_path="")
    await session.commit()

    _workspace(
        app,
        ws_root,
        [
            RepoEntry(path="legacy", alias="legacy", is_primary=True),
            RepoEntry(path="broken", alias="broken"),
            RepoEntry(path="fresh", alias="fresh"),
            RepoEntry(path="gone", alias="gone"),
        ],
    )
    _git_dir(ws_root / "fresh")

    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}

    # No docs field recorded at all: the legacy docs_enabled default holds.
    assert rows[legacy_row.id]["workspace_alias"] == "legacy"
    assert rows[legacy_row.id]["is_primary"] is True
    assert rows[legacy_row.id]["docs_enabled"] is True
    assert rows[legacy_row.id]["run_mode"] == "fast"
    assert rows[legacy_row.id]["git_tier"] == "essential"
    # An unreadable state file leaves the docs fields unset, never a 500.
    assert rows[broken_row.id]["workspace_alias"] == "broken"
    assert rows[broken_row.id]["docs_enabled"] is None
    assert rows[broken_row.id]["docs_mode"] is None
    # Registered repos outside the workspace, or with no path, get no alias.
    assert rows[outside_row.id]["workspace_alias"] is None
    assert rows[no_path_row.id]["workspace_alias"] is None

    fresh = rows["ws:fresh"]
    assert fresh["workspace_status"] == "needs_index"
    assert fresh["name"] == "fresh"
    assert fresh["default_branch"] == "main"
    assert fresh["head_commit"] is None
    assert fresh["docs_enabled"] is False
    assert fresh["docs_mode"] == "none"
    assert fresh["docs_skip_reason"] == "not indexed yet"
    assert fresh["is_primary"] is False
    assert rows["ws:gone"]["workspace_status"] == "missing_dir"
    assert "ws:legacy" not in rows


# ---------------------------------------------------------------------------
# GET /api/repos/summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_freshness_when_one_side_is_unknown(
    client: AsyncClient, session, tmp_path: Path
) -> None:
    no_path = await crud.upsert_repository(session, name="nopath", local_path="")
    plain = tmp_path / "plain"
    plain.mkdir()
    no_git = await crud.upsert_repository(session, name="plain", local_path=str(plain))
    live_only_dir = _git_dir(tmp_path / "live")
    (live_only_dir / ".git" / "HEAD").write_text("b" * 40 + "\n", encoding="utf-8")
    live_only = await crud.upsert_repository(
        session, name="live", local_path=str(live_only_dir)
    )
    # Registration stamps head_commit from the live HEAD; clear it so no side
    # records what the index reflects.
    live_only.head_commit = None
    behind_dir = _git_dir(tmp_path / "behind")
    (behind_dir / ".git" / "HEAD").write_text("c" * 40 + "\n", encoding="utf-8")
    (behind_dir / ".repowise").mkdir()
    (behind_dir / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": "d" * 40}), encoding="utf-8"
    )
    behind = await crud.upsert_repository(session, name="behind", local_path=str(behind_dir))
    await session.commit()

    resp = await client.get("/api/repos/summary")
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()["repos"]}

    for rid in (no_path.id, no_git.id):
        assert (
            rows[rid]["indexed_commit"],
            rows[rid]["live_head"],
            rows[rid]["index_behind"],
        ) == (None, None, None)
    assert rows[live_only.id]["indexed_commit"] is None
    assert rows[live_only.id]["live_head"] == "b" * 12
    assert rows[live_only.id]["index_behind"] is None
    assert rows[behind.id]["indexed_commit"] == "d" * 12
    assert rows[behind.id]["live_head"] == "c" * 12
    assert rows[behind.id]["index_behind"] is True
    assert rows[behind.id]["status"] == "needs_index"


@pytest.mark.asyncio
async def test_summary_fans_out_to_workspace_databases(
    client: AsyncClient, app, session, tmp_path: Path
) -> None:
    primary = await crud.upsert_repository(
        session, name="primary", local_path=str(_git_dir(tmp_path / "primary"))
    )
    session.add(GraphNode(repository_id=primary.id, node_id="p.py", node_type="file"))
    await session.commit()
    engine, factory, other_id = await _second_db(_git_dir(tmp_path / "other"))

    opened: list[str] = []

    def recording_factory():
        opened.append("primary")
        return factory()

    app.state.workspace_sessions = {
        primary.id: recording_factory,  # already counted from the primary DB
        other_id: factory,
        "broken": _broken_factory,
    }
    try:
        resp = await client.get("/api/repos/summary")
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()["repos"]}
    assert rows[primary.id]["file_count"] == 1
    assert rows[other_id]["file_count"] == 1
    assert rows[other_id]["status"] == "indexed"
    # Its figures came from the primary DB, so neither pass reopens it.
    assert opened == []


# ---------------------------------------------------------------------------
# PATCH / DELETE / stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_repo_applies_each_field(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"url": "https://example.com/r", "default_branch": "trunk", "settings": {"a": 1}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "test-repo"
    assert body["url"] == "https://example.com/r"
    assert body["default_branch"] == "trunk"
    assert body["settings"] == {"a": 1}

    missing = await client.patch("/api/repos/nope", json={"name": "x"})
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Repository not found"}


@pytest.mark.asyncio
async def test_update_repo_unknown_style_message(client: AsyncClient) -> None:
    from repowise.core.generation.styles import list_styles

    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}", json={"settings": {"wiki_style": "bogus"}}
    )
    valid = ", ".join(s.name for s in list_styles())
    assert resp.status_code == 400
    assert resp.json() == {
        "detail": f"Unknown wiki_style 'bogus'. Valid styles: {valid}."
    }


@pytest.mark.asyncio
async def test_delete_synthetic_id_outside_workspace_is_404(client: AsyncClient, app) -> None:
    assert (await client.delete("/api/repos/ws:alias")).status_code == 404
    _workspace(app, Path("."), [RepoEntry(path="x", alias="x")])
    resp = await client.delete("/api/repos/ws:unknown")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Repository not found"}


@pytest.mark.asyncio
async def test_delete_workspace_repo_drops_routing_and_registry_row(
    client: AsyncClient, app, session, tmp_path: Path
) -> None:
    engine, factory, repo_id = await _second_db(_git_dir(tmp_path / "member"))
    async with get_session(factory) as ws_session:
        await crud.upsert_page(
            ws_session,
            page_id="module_page:src",
            repository_id=repo_id,
            page_type="module_page",
            title="src",
            content="body",
            summary="s",
            target_path="src",
            source_hash="h",
            model_name="m",
            provider_name="p",
        )
    session.add(Repository(id=repo_id, name="member", local_path=str(tmp_path / "member")))
    await session.commit()

    repo_fts = SimpleNamespace(delete_many=AsyncMock())
    app.state.workspace_sessions = {repo_id: factory}
    app.state.workspace_fts = {repo_id: repo_fts}
    try:
        resp = await client.delete(f"/api/repos/{repo_id}")
    finally:
        await engine.dispose()

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted_pages": 1}
    repo_fts.delete_many.assert_awaited_once_with(["module_page:src"])
    assert repo_id not in app.state.workspace_sessions
    assert repo_id not in app.state.workspace_fts
    async with get_session(app.state.session_factory) as primary:
        assert await crud.get_repository(primary, repo_id) is None


@pytest.mark.asyncio
async def test_delete_workspace_repo_survives_unreadable_primary(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    engine, factory, repo_id = await _second_db(_git_dir(tmp_path / "member"))
    app.state.workspace_sessions = {repo_id: factory}
    real_primary = app.state.session_factory
    app.state.session_factory = _broken_factory
    try:
        resp = await client.delete(f"/api/repos/{repo_id}")
    finally:
        app.state.session_factory = real_primary
        await engine.dispose()

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted_pages": 0}
    assert repo_id not in app.state.workspace_sessions


@pytest.mark.asyncio
async def test_stats_and_full_resync_404(client: AsyncClient) -> None:
    for method, url in (
        ("GET", "/api/repos/nope/stats"),
        ("POST", "/api/repos/nope/full-resync"),
        ("POST", "/api/repos/nope/index"),
        ("POST", "/api/repos/nope/preflight"),
        ("POST", "/api/repos/nope/generate/estimate"),
        ("GET", "/api/repos/nope/file-content?file_path=a.py"),
    ):
        resp = await client.request(method, url, json={} if method == "POST" else None)
        assert resp.status_code == 404, url
        assert resp.json() == {"detail": "Repository not found"}, url


@pytest.mark.asyncio
async def test_stats_counts(client: AsyncClient, session) -> None:
    repo = await create_test_repo(client)
    session.add_all(
        [
            GraphNode(repository_id=repo["id"], node_id="a.py", node_type="file", symbol_count=2),
            GraphNode(
                repository_id=repo["id"],
                node_id="a.py::f",
                node_type="symbol",
                symbol_count=0,
                is_entry_point=True,
            ),
        ]
    )
    await session.commit()

    resp = await client.get(f"/api/repos/{repo['id']}/stats")
    assert resp.status_code == 200
    assert resp.json() == {
        "file_count": 1,
        "symbol_count": 2,
        "entry_point_count": 1,
        "doc_coverage_pct": 0.0,
        "freshness_score": 0.0,
        "dead_export_count": 0,
    }


# ---------------------------------------------------------------------------
# Generate selection (pure helpers)
# ---------------------------------------------------------------------------


def _body(**selection) -> repos_module.GenerateRequestBody:
    return repos_module.GenerateRequestBody(
        selection=repos_module.GenerateSelectionBody(**selection)
    )


@pytest.mark.parametrize(
    ("selection", "detail"),
    [
        (
            {"kind": "ranked"},
            "A ranked selection needs exactly one of coverage_pct or top_n.",
        ),
        (
            {"kind": "ranked", "coverage_pct": 0.5, "top_n": 3},
            "A ranked selection needs exactly one of coverage_pct or top_n.",
        ),
        (
            {"kind": "ranked", "coverage_pct": 1.5},
            "coverage_pct must be a fraction in (0, 1] (0.2 == the top 20%, 1.0 == all).",
        ),
        ({"kind": "ranked", "top_n": 0}, "top_n must be a positive number of pages."),
        (
            {"kind": "ranked", "top_n": 3, "path_prefix": "src"},
            "A ranked selection cannot also carry page_ids or path_prefix.",
        ),
        (
            {"kind": "stale", "top_n": 3},
            'coverage_pct / top_n rank pages by importance and require selection kind "ranked", not "stale".',
        ),
        (
            {"kind": "page_ids", "page_ids": ["module_page:src", "file_page:a.py"]},
            "generate writes the concept layer only; these pages render from structure "
            "and refresh on update, not generate: file_page:a.py",
        ),
    ],
)
def test_generate_selection_rejections(selection: dict, detail: str) -> None:
    with pytest.raises(HTTPException) as exc:
        repos_module._validate_generate_selection(
            repos_module.GenerateSelectionBody(**selection)
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == detail


@pytest.mark.parametrize(
    "selection",
    [
        {"kind": "unwritten"},
        {"kind": "ranked", "coverage_pct": 1.0},
        {"kind": "ranked", "top_n": 5},
        {"kind": "page_ids", "page_ids": ["module_page:src"]},
    ],
)
def test_generate_selection_accepts(selection: dict) -> None:
    repos_module._validate_generate_selection(repos_module.GenerateSelectionBody(**selection))


def test_generate_job_config_per_kind() -> None:
    assert repos_module._generate_job_config(_body(kind="page_ids")) == {
        "mode": "generate",
        "selection": {"kind": "page_ids", "page_ids": []},
        "cascade": None,
    }
    assert repos_module._generate_job_config(_body(kind="path_prefix", path_prefix="src"))[
        "selection"
    ] == {"kind": "path_prefix", "path_prefix": "src"}
    assert repos_module._generate_job_config(_body(kind="ranked", top_n=4))["selection"] == {
        "kind": "ranked",
        "top_n": 4,
    }
    assert repos_module._generate_job_config(_body(kind="ranked", coverage_pct=0.3))[
        "selection"
    ] == {"kind": "ranked", "coverage_pct": 0.3}
    styled = repos_module.GenerateRequestBody(cascade="full", style="caveman")
    assert repos_module._generate_job_config(styled) == {
        "mode": "generate",
        "selection": {"kind": "unwritten"},
        "cascade": "full",
        "style": "caveman",
    }


def test_generate_style_validation() -> None:
    repos_module._validate_generate_style(None)
    repos_module._validate_generate_style("caveman")
    with pytest.raises(HTTPException) as exc:
        repos_module._validate_generate_style("bogus")
    assert exc.value.status_code == 400
    assert exc.value.detail.startswith("Unknown style 'bogus'. Valid styles: ")


# ---------------------------------------------------------------------------
# POST /generate/estimate
# ---------------------------------------------------------------------------


def _provider(**extra):
    return SimpleNamespace(provider_name="mock", model_name="claude-sonnet-4-6", **extra)


def _cost(total_pages: int = 0):
    return SimpleNamespace(
        total_pages=total_pages,
        estimated_cost_usd=1.234567,
        cost_range=SimpleNamespace(low=1.00004, high=2.00006),
        estimated_input_tokens=100,
        estimated_output_tokens=40,
        is_calibrated=False,
    )


@pytest.mark.asyncio
async def test_estimate_prices_the_resolved_scope(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    plan = SimpleNamespace(
        cost_plans=[
            SimpleNamespace(page_type="module_page", count=3),
            SimpleNamespace(page_type="repo_overview", count=1),
        ],
        stale_ids=["a", "b"],
        unknown_page_ids=("zzz",),
    )
    with (
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            return_value=_provider(),
        ),
        patch(
            "repowise.core.pipeline.scoped_generation.rehydrate_repo",
            new=AsyncMock(return_value=object()),
        ),
        patch("repowise.server.job_executor._resolve_generate_scope", return_value=plan),
        patch("repowise.core.cost_estimator.estimate_cost", return_value=_cost()) as cost,
    ):
        resp = await client.post(
            f"/api/repos/{repo['id']}/generate/estimate",
            json={"selection": {"kind": "ranked", "top_n": 4}},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "total_pages": 4,
        "pages_by_type": {"module_page": 3, "repo_overview": 1},
        "pages_to_mark_stale": 2,
        "unknown_page_ids": ["zzz"],
        "provider": {"name": "mock", "model": "claude-sonnet-4-6", "error": None},
        "estimate": {
            "estimated_cost_usd": 1.2346,
            "cost_low_usd": 1.0,
            "cost_high_usd": 2.0001,
            "estimated_input_tokens": 100,
            "estimated_output_tokens": 40,
            "is_calibrated": False,
        },
    }
    assert cost.call_args.args[1:] == ("mock", "claude-sonnet-4-6")


@pytest.mark.asyncio
async def test_estimate_without_cost_plans_has_no_estimate(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    plan = SimpleNamespace(cost_plans=[], stale_ids=[], unknown_page_ids=[])
    with (
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            return_value=_provider(),
        ),
        patch(
            "repowise.core.pipeline.scoped_generation.rehydrate_repo",
            new=AsyncMock(return_value=object()),
        ),
        patch("repowise.server.job_executor._resolve_generate_scope", return_value=plan),
    ):
        resp = await client.post(f"/api/repos/{repo['id']}/generate/estimate", json={})

    assert resp.json()["estimate"] is None
    assert resp.json()["total_pages"] == 0
    assert "note" not in resp.json()


@pytest.mark.asyncio
async def test_estimate_reports_rehydrate_failure_as_a_note(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    with (
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            return_value=_provider(),
        ),
        patch(
            "repowise.core.pipeline.scoped_generation.rehydrate_repo",
            new=AsyncMock(side_effect=RuntimeError("no persisted graph")),
        ),
    ):
        resp = await client.post(f"/api/repos/{repo['id']}/generate/estimate", json={})

    assert resp.status_code == 200
    assert resp.json() == {
        "total_pages": 0,
        "pages_by_type": {},
        "pages_to_mark_stale": 0,
        "unknown_page_ids": [],
        "provider": {"name": "mock", "model": "claude-sonnet-4-6", "error": None},
        "estimate": None,
        "note": "no persisted graph",
    }


# ---------------------------------------------------------------------------
# POST /index and /preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_tolerates_unparseable_settings(
    client: AsyncClient, app, session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(repos_module, "execute_job", _noop_execute)
    repo_dir = _git_dir(tmp_path / "r")
    (repo_dir / "a.py").write_text("x = 1\n")
    row = await crud.upsert_repository(session, name="r", local_path=str(repo_dir))
    row.settings_json = "{not json"
    await session.commit()

    resp = await client.post(f"/api/repos/{row.id}/index")

    assert resp.status_code == 202
    assert set(resp.json()) == {"job_id", "status", "stream_token"}
    assert resp.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_preflight_probe_failure_and_file_count_failure(
    client: AsyncClient, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    failing = _provider(generate=AsyncMock(side_effect=RuntimeError("401 bad key")))
    with (
        patch(
            "repowise.server.provider_config.get_chat_provider_instance",
            return_value=failing,
        ),
        patch("repowise.core.ingestion.FileTraverser", side_effect=OSError("walk failed")),
        patch(
            "repowise.core.cost_estimator.estimate_cost", return_value=_cost(total_pages=7)
        ) as cost,
    ):
        resp = await client.post(f"/api/repos/{repo['id']}/preflight?coverage_pct=0.5")

    assert resp.status_code == 200
    assert resp.json() == {
        "provider": {
            "ok": False,
            "name": "mock",
            "model": "claude-sonnet-4-6",
            "error": "401 bad key",
        },
        "file_count": 0,
        "estimate": {
            "total_pages": 7,
            "estimated_cost_usd": 1.2346,
            "cost_low_usd": 1.0,
            "cost_high_usd": 2.0001,
            "estimated_input_tokens": 100,
            "estimated_output_tokens": 40,
            "is_calibrated": False,
            "coverage_pct": 0.5,
        },
    }
    assert cost.call_args.args[1:] == ("mock", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# _launch_job_task failure paths
# ---------------------------------------------------------------------------


async def _pending_job(session_factory, tmp_path: Path) -> tuple[str, str]:
    async with get_session(session_factory) as session:
        repo = await crud.upsert_repository(session, name="r", local_path=str(tmp_path))
        job = await crud.upsert_generation_job(session, repository_id=repo.id, status="pending")
        return repo.id, job.id


async def _drain(state) -> None:
    for _ in range(50):
        await asyncio.sleep(0)
        if not state.background_tasks:
            return
        await asyncio.gather(*list(state.background_tasks), return_exceptions=True)


async def _job(session_factory, job_id: str):
    async with get_session(session_factory) as session:
        return await crud.get_generation_job(session, job_id)


def _request(app) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=app.state))


@pytest.mark.asyncio
async def test_launch_marks_a_crashed_task_failed(app, tmp_path: Path, monkeypatch) -> None:
    repo_id, job_id = await _pending_job(app.state.session_factory, tmp_path)

    async def crash(job_id, app_state, session_factory_override=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(repos_module, "execute_job", crash)
    repos_module._launch_job_task(_request(app), job_id, repo_id)
    assert job_id in app.state.job_tasks
    await _drain(app.state)

    job = await _job(app.state.session_factory, job_id)
    assert job.status == "failed"
    assert job.error_message == "Background task crashed: boom"
    assert job_id not in app.state.job_tasks


@pytest.mark.asyncio
async def test_launch_marks_an_early_cancel_cancelled(app, tmp_path: Path, monkeypatch) -> None:
    repo_id, job_id = await _pending_job(app.state.session_factory, tmp_path)
    monkeypatch.setattr(repos_module, "execute_job", _noop_execute)

    repos_module._launch_job_task(_request(app), job_id, repo_id)
    app.state.job_tasks[job_id].cancel()
    await _drain(app.state)

    job = await _job(app.state.session_factory, job_id)
    assert job.status == "cancelled"
    assert job.error_message == "Cancelled by user"


@pytest.mark.asyncio
async def test_launch_records_a_task_that_could_not_be_created(
    app, tmp_path: Path, monkeypatch
) -> None:
    repo_id, job_id = await _pending_job(app.state.session_factory, tmp_path)
    monkeypatch.setattr(repos_module, "execute_job", _noop_execute)
    real_create_task = asyncio.create_task

    def refuse_job_tasks(coro, *, name=None, **kwargs):
        if name and name.startswith("job-"):
            coro.close()
            raise RuntimeError("loop closing")
        return real_create_task(coro, name=name, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", refuse_job_tasks)
    repos_module._launch_job_task(_request(app), job_id, repo_id)
    monkeypatch.setattr(asyncio, "create_task", real_create_task)
    await _drain(app.state)

    job = await _job(app.state.session_factory, job_id)
    assert job.status == "failed"
    assert job.error_message == "Failed to launch background task: loop closing"
    assert job_id not in getattr(app.state, "job_tasks", {})


# ---------------------------------------------------------------------------
# GET /export and /file-content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_without_pages_is_404(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/export")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "No pages to export"}


@pytest.mark.asyncio
async def test_export_path_mapping(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_page(
            session,
            page_id="symbol_spotlight:a.py::f",
            repository_id=repo["id"],
            page_type="symbol_spotlight",
            title="f",
            content="body",
            summary="s",
            target_path="src\\a.py::f->g",
            source_hash="h",
            model_name="m",
            provider_name="p",
        )
    resp = await client.get(f"/api/repos/{repo['id']}/export")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="test-repo-wiki.zip"'
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert zf.namelist() == ["wiki/symbol_spotlight/src/a.py/f--g.md"]
    assert zf.read(zf.namelist()[0]).decode() == "# f\n\nbody"


@pytest.mark.asyncio
async def test_file_content_indexed_by_health_metrics_only(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    root = Path(repo["local_path"])
    (root / "m.py").write_text("y = 2\n")
    async with get_session(app.state.session_factory) as session:
        session.add(HealthFileMetric(repository_id=repo["id"], file_path="m.py"))
        session.add(HealthFileMetric(repository_id=repo["id"], file_path="gone.py"))

    ok = await client.get(
        f"/api/repos/{repo['id']}/file-content", params={"file_path": "m.py"}
    )
    assert ok.status_code == 200
    assert ok.text == "y = 2\n"

    gone = await client.get(
        f"/api/repos/{repo['id']}/file-content", params={"file_path": "gone.py"}
    )
    assert gone.status_code == 404
    assert gone.json() == {"detail": "File not found"}


@pytest.mark.asyncio
async def test_file_content_read_error_is_500(
    client: AsyncClient, app, tmp_path: Path, monkeypatch
) -> None:
    repo = await create_test_repo(client, tmp_path)
    root = Path(repo["local_path"])
    (root / "m.py").write_text("y = 2\n")
    async with get_session(app.state.session_factory) as session:
        session.add(GraphNode(repository_id=repo["id"], node_id="m.py", node_type="file"))

    def denied(self, *args, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(Path, "read_text", denied)
    resp = await client.get(
        f"/api/repos/{repo['id']}/file-content", params={"file_path": "m.py"}
    )
    monkeypatch.undo()
    assert resp.status_code == 500
    assert resp.json() == {"detail": "access denied"}

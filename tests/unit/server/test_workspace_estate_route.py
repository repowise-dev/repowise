"""GET /api/workspace/estate: the estate report document, as the CLI builds it."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from repowise.core.persistence.models import Repository
from repowise.core.report.estate import build_estate_document
from repowise.core.workspace.config import WorkspaceConfig
from repowise.core.workspace.registry import open_repo_db
from repowise.core.workspace.system_graph import (
    SystemEdge,
    SystemGraph,
    SystemNode,
    save_system_graph,
)
from repowise.server.routers import workspace
from repowise.server.schemas.workspace import WorkspaceEstateResponse

_SECTIONS = [
    "estate-summary",
    "estate-map",
    "estate-contracts",
    "estate-health",
    "estate-seams",
    "estate-start",
]


def _app(root: Path | None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.workspace_config = WorkspaceConfig.load(root) if root else None
    app.state.cross_repo_enricher = None
    app.state.workspace_root = str(root) if root else None
    app.include_router(workspace.router)
    return app


async def _get(app: FastAPI):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get("/api/workspace/estate")


async def _workspace(root: Path, *, graph: bool = True) -> Path:
    """web (indexed) calls api (not indexed)."""
    (root / ".repowise-workspace.yaml").write_text(
        "version: 1\nrepos:\n  - path: web\n    alias: web\n  - path: api\n    alias: api\n",
        encoding="utf-8",
    )
    (root / "api").mkdir()
    web = root / "web"
    (web / ".repowise").mkdir(parents=True)
    engine, factory = await open_repo_db(web)
    async with factory() as session:
        session.add(
            Repository(id="r-web", name="web", url="", local_path=str(web), head_commit="a" * 40)
        )
        await session.commit()
    await engine.dispose()
    if graph:
        save_system_graph(
            SystemGraph(
                generated_at="2026-10-01T00:00:00+00:00",
                nodes=[
                    SystemNode(id="web", repo="web", service_path=None, name="web"),
                    SystemNode(id="api", repo="api", service_path=None, name="api"),
                ],
                edges=[
                    SystemEdge(
                        id="web->api", source="web", target="api", kind="http", weight=2,
                        confidence=0.9, match_type="exact", structural=True,
                    )
                ],
            ),
            root,
        )
    return root


@pytest.mark.asyncio
async def test_estate_route_serves_the_document_the_cli_builds(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = await _workspace(tmp_path.resolve())
    resp = await _get(_app(root))
    assert resp.status_code == 200, resp.text
    served = resp.json()
    built = WorkspaceEstateResponse(**await build_estate_document(root)).model_dump(mode="json")
    assert served["kind"] == "estate" and served["scope"] == {"kind": "workspace", "name": root.name}
    assert [s["id"] for s in served["sections"]] == _SECTIONS
    assert served["sections"] == built["sections"] and served["repos"] == built["repos"]
    web, api = served["repos"]
    assert web["alias"] == "web" and web["head_commit"] == "a" * 40 and web["indexed"] is True
    assert api == {
        "alias": "api",
        "name": "api",
        "head_commit": None,
        "last_snapshot": None,
        "indexed": False,
        "index_state": None,
    }
    summary = {c["name"]: c for c in served["sections"][0]["checks"]}
    rows = {r["alias"]: r for r in summary["repositories"]["data"]["repositories"]}
    assert rows["web"]["indexed"] and not rows["api"]["indexed"]


@pytest.mark.asyncio
async def test_without_a_system_graph_the_graph_sections_are_inconclusive(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = await _workspace(tmp_path.resolve(), graph=False)
    resp = await _get(_app(root))
    assert resp.status_code == 200, resp.text
    sections = {s["id"]: s for s in resp.json()["sections"]}
    (missing,) = sections["estate-summary"]["checks"]
    assert missing["basis"] == "unresolved" and missing["score"] == -1
    assert "repowise update --workspace" in missing["reason"]


@pytest.mark.asyncio
async def test_estate_route_outside_a_workspace_is_404():
    resp = await _get(_app(None))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_member_index_behind_this_version_is_named_not_called_unindexed(
    tmp_path, monkeypatch
):
    import repowise.core.persistence.database as database
    from repowise.core.report.render import report_renderers

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = await _workspace(tmp_path.resolve())

    async def behind(engine):
        return True

    monkeypatch.setattr(database, "schema_behind", behind)
    resp = await _get(_app(root))
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    web, api = doc["repos"]
    assert web["indexed"] is True and web["index_state"] == "behind" and web["head_commit"] is None
    assert api["indexed"] is False and api["index_state"] is None
    summary = {c["name"]: c for c in doc["sections"][0]["checks"]}["repositories"]
    assert any("older repowise" in d and "web" in d for d in summary["details"])
    assert not any("not indexed" in d and "web" in d for d in summary["details"])
    assert "index behind this version" in report_renderers.get("markdown")(doc)


@pytest.mark.asyncio
async def test_an_unreadable_member_index_is_named_and_other_errors_propagate(
    tmp_path, monkeypatch
):
    import repowise.core.workspace.repo_index as repo_index

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = await _workspace(tmp_path.resolve())
    (root / "api" / ".repowise").mkdir()
    (root / "api" / ".repowise" / "wiki.db").write_bytes(b"not a sqlite database" * 64)
    doc = await build_estate_document(root)
    web, api = doc["repos"]
    assert web["head_commit"] == "a" * 40
    assert api == {"alias": "api", "name": "api", "index_state": "unreadable"}

    async def broken(session, path):
        raise RuntimeError("a defect, not a missing index")

    monkeypatch.setattr(repo_index, "checkout_repository", broken)
    with pytest.raises(RuntimeError):
        await build_estate_document(root)

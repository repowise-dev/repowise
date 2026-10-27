"""GET /api/workspace/system-graph/links over a real enricher and stored artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from repowise.server.mcp_server._enrichment import CrossRepoEnricher

from .test_workspace_router import _make_workspace_app, _make_ws_config, _write_json

FIXTURE = Path(__file__).parents[2] / "fixtures" / "workspace_links" / "eshop.json"


def _enricher(tmp_path: Path) -> CrossRepoEnricher:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _write_json(
        tmp_path / "contracts.json",
        {
            "version": 15,
            "contracts": data["contracts"],
            "contract_links": data["contract_links"],
            "service_links": data["service_links"],
            "repo_provenance": data["repo_provenance"],
        },
    )
    _write_json(
        tmp_path / "cross_repo_edges.json",
        {"version": 2, "package_deps": data["package_deps"], "co_changes": data["co_changes"]},
    )
    _write_json(
        tmp_path / "system_graph.json",
        {
            "version": 2,
            "nodes": data["nodes"],
            "edges": [],
            "diagnostics": {},
            "repo_provenance": data["repo_provenance"],
        },
    )
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        contracts_path=tmp_path / "contracts.json",
        system_graph_path=tmp_path / "system_graph.json",
    )


async def _get(app, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get("/api/workspace/system-graph/links", params=params)


@pytest.mark.asyncio
async def test_pages_the_links_between_two_services(tmp_path: Path) -> None:
    app = _make_workspace_app(ws_config=_make_ws_config(), enricher=_enricher(tmp_path))
    resp = await _get(app, a="webapp", b="ordering.api", limit=5)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 8
    assert len(data["links"]) == 5
    assert {t["kind"]: t["count"] for t in data["totals"]} == {"event": 6, "http": 2}
    row = data["links"][0]
    assert row["direction"] == "a_to_b"
    assert row["kind"] == "event"
    assert row["link"]["strategy"] == "exact_id"
    assert data["stale_graph"] is False


@pytest.mark.asyncio
async def test_gateway_service_link_is_served(tmp_path: Path) -> None:
    app = _make_workspace_app(ws_config=_make_ws_config(), enricher=_enricher(tmp_path))
    data = (await _get(app, a="webapp", b="catalog.api", kind="http")).json()
    assert "gateway_route" in {row["link"]["strategy"] for row in data["links"]}


@pytest.mark.asyncio
async def test_package_dependency_rows_are_served(tmp_path: Path) -> None:
    app = _make_workspace_app(ws_config=_make_ws_config(), enricher=_enricher(tmp_path))
    data = (await _get(app, a="webapp", b="eventbusrabbitmq")).json()
    assert data["total"] == 1
    row = data["links"][0]
    assert (row["evidence"], row["kind"], row["direction"]) == ("package", "package", "a_to_b")
    assert row["package_dep"]["target_repo"] == "eventbusrabbitmq"


@pytest.mark.asyncio
async def test_unknown_service_is_404(tmp_path: Path) -> None:
    app = _make_workspace_app(ws_config=_make_ws_config(), enricher=_enricher(tmp_path))
    assert (await _get(app, a="webapp", b="nope")).status_code == 404


@pytest.mark.asyncio
async def test_no_graph_is_404() -> None:
    app = _make_workspace_app(ws_config=_make_ws_config(), enricher=None)
    assert (await _get(app, a="webapp", b="ordering.api")).status_code == 404


@pytest.mark.asyncio
async def test_not_workspace_mode_and_missing_params() -> None:
    assert (await _get(_make_workspace_app(), a="x", b="y")).status_code == 404
    app = _make_workspace_app(ws_config=_make_ws_config(), enricher=None)
    assert (await _get(app, a="webapp")).status_code == 422

"""/api/telemetry/view: one anonymous ``view_opened`` per web view, from a fixed list."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.platform import telemetry as core_telemetry
from repowise.server.routers import telemetry

_NAV_ITEMS = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "web"
    / "src"
    / "components"
    / "layout"
    / "nav-items.ts"
)


@pytest.fixture
async def telemetry_client(app, client: AsyncClient) -> AsyncClient:
    app.include_router(telemetry.router)
    return client


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        core_telemetry, "record_event", lambda event, props: calls.append((event, props))
    )
    return calls


@pytest.mark.anyio
async def test_a_known_view_is_recorded(telemetry_client: AsyncClient, recorded: list) -> None:
    resp = await telemetry_client.post("/api/telemetry/view", json={"view": "repo.codeHealth"})

    assert resp.status_code == 204
    assert recorded == [("view_opened", {"view": "repo.codeHealth"})]


@pytest.mark.anyio
@pytest.mark.parametrize("view", ["repo.secret-repo", "/repos/r1/files/src/a.py", "", "x" * 65])
async def test_anything_else_is_refused_and_never_recorded(
    telemetry_client: AsyncClient, recorded: list, view: str
) -> None:
    resp = await telemetry_client.post("/api/telemetry/view", json={"view": view})

    assert resp.status_code == 422
    assert recorded == []


@pytest.mark.anyio
@pytest.mark.parametrize("switch", ["DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", None])
async def test_the_opt_out_silences_the_event(
    telemetry_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    switch: str | None,
) -> None:
    for name in ("DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", "REPOWISE_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REPOWISE_TELEMETRY_DEBUG", "1")
    if switch:
        monkeypatch.setenv(switch, "1")

    resp = await telemetry_client.post("/api/telemetry/view", json={"view": "global.dashboard"})

    assert resp.status_code == 204
    dumped = capsys.readouterr().err
    if switch:
        assert "would send" not in dumped
    else:
        envelope = json.loads(dumped.split("would send:\n", 1)[1])
        assert envelope["event"] == "view_opened"
        assert envelope["properties"]["view"] == "global.dashboard"


def _nav_keys(source: str, start: str, end: str) -> set[str]:
    """``labelKey`` of every nav item (one per ``href``) between two markers."""
    section = source.split(start, 1)[1].split(end, 1)[0]
    keys = [
        m.group(1)
        for line in section.splitlines()
        if "href:" in line
        for m in [re.search(r'labelKey: "([^"]+)"', line)]
        if m
    ]
    # An item laid out over several lines would be skipped above; fail instead.
    assert keys and len(keys) == section.count("href:"), start
    return set(keys)


def test_the_view_list_matches_the_web_nav() -> None:
    source = _NAV_ITEMS.read_text(encoding="utf-8")
    g, w = "export const GLOBAL_NAV", "export const WORKSPACE_NAV"
    r, flat = "export function repoNavGroups", "export function repoNavItems"
    nav = {
        *(f"global.{k}" for k in _nav_keys(source, g, w)),
        *(f"workspace.{k}" for k in _nav_keys(source, w, r)),
        *(f"repo.{k}" for k in _nav_keys(source, r, flat)),
    }
    assert nav == telemetry.VIEWS


def test_every_view_is_documented() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "reference" / "TELEMETRY.md").read_text(
        encoding="utf-8"
    )
    row = next(line for line in doc.splitlines() if line.startswith("| `properties.view`"))
    documented = {
        f"{scope}.{name}"
        for scope, names in re.findall(r"`(\w+)\.` \(([^)]*)\)", row)
        for name in re.findall(r"`(\w+)`", names)
    }
    assert documented == telemetry.VIEWS

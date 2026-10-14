"""Workspace target routing for get_context, get_symbol, get_risk and get_health.

Builds on the two-repo ``workspace_mcp`` fixture (backend is the default, both
repos have ``src/probe.py``) and adds a third repo so a path can be ambiguous.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import _state
from repowise.server.mcp_server._workspace_routing import Route, route_targets
from tests.unit.server.test_mcp_workspace import (  # noqa: F401  (fixture)
    _make_repo_context,
    workspace_mcp,
)


@pytest.fixture
async def routed_ws(workspace_mcp):  # noqa: F811
    root = workspace_mcp.workspace_root
    docs_root = root / "docs-site"
    (docs_root / "src").mkdir(parents=True)
    workspace_mcp._contexts["docs"] = await _make_repo_context("docs", str(docs_root), pages=[])
    workspace_mcp.ws_config.repos.append(SimpleNamespace(alias="docs", path="docs-site"))
    (root / "web-client/src/only_fe.py").write_text("def only_fe():\n    return 1\n")
    for repo in ("web-client", "docs-site"):
        (root / repo / "src/shared.py").write_text("def shared():\n    return 2\n")
    return workspace_mcp


def _by_target(routes: list[Route]) -> dict[str, Route]:
    return {r.target: r for r in routes}


# -- the router -------------------------------------------------------------


def test_router_is_off_outside_workspace_mode():
    assert _state._registry is None
    assert route_targets(["src/probe.py", "frontend:src/probe.py"]) is None


async def test_router_rules(routed_ws):
    abs_fe = str(routed_ws.workspace_root / "web-client" / "src" / "only_fe.py")
    routes = _by_target(
        route_targets(
            [
                "src/probe.py",
                "src/only_fe.py",
                "FRONTEND:src/probe.py::frontend_probe",
                "src/shared.py",
                "src/nowhere.py",
                "module:src",
                "backend_probe",
                abs_fe,
                "src/only_fe.py:1-2",
            ]
        )
    )
    # Present in the default repo: served there, as before.
    assert routes["src/probe.py"] == Route("src/probe.py", "src/probe.py", "backend")
    assert routes["src/only_fe.py"].repo == "frontend"
    assert routes["src/only_fe.py"].routed
    prefixed = routes["FRONTEND:src/probe.py::frontend_probe"]
    assert (prefixed.repo, prefixed.local) == ("frontend", "src/probe.py::frontend_probe")
    assert routes["src/shared.py"].repo is None
    assert routes["src/shared.py"].candidates == ("frontend", "docs")
    assert routes["src/nowhere.py"].repo == "backend"
    assert routes["src/nowhere.py"].missed
    # Target kinds and bare names never leave the default repo.
    assert routes["module:src"] == Route("module:src", "module:src", "backend")
    assert routes["backend_probe"] == Route("backend_probe", "backend_probe", "backend")
    assert (routes[abs_fe].repo, routes[abs_fe].local) == ("frontend", "src/only_fe.py")
    assert routes["src/only_fe.py:1-2"].repo == "frontend"


async def test_router_never_lets_a_path_escape_its_repo(routed_ws):
    (route,) = route_targets(["../web-client/src/only_fe.py"])
    assert (route.repo, route.missed) == ("backend", True)


# -- get_context --------------------------------------------------------------


async def test_get_context_default_repo_shape_unchanged(routed_ws):
    from repowise.server.mcp_server import get_context

    implicit = await get_context(targets=["src/probe.py"])
    explicit = await get_context(targets=["src/probe.py"], repo="backend")
    assert implicit["targets"] == explicit["targets"]
    assert "repo" not in implicit["targets"]["src/probe.py"]
    assert set(implicit) == set(explicit)


async def test_get_context_routes_a_mixed_batch(routed_ws):
    from repowise.server.mcp_server import get_context

    targets = [
        "src/probe.py",
        "frontend:src/probe.py",
        "src/only_fe.py",
        "src/shared.py",
        "src/nowhere.py",
    ]
    result = await get_context(targets=targets)
    cards = result["targets"]

    assert list(cards) == targets
    assert "repo" not in cards["src/probe.py"]
    assert cards["src/probe.py"]["docs"]["title"] == "Backend Probe"
    assert cards["frontend:src/probe.py"]["repo"] == "frontend"
    assert cards["frontend:src/probe.py"]["docs"]["title"] == "Frontend Probe"
    assert cards["src/only_fe.py"]["repo"] == "frontend"
    assert cards["src/shared.py"]["candidates"] == ["frontend", "docs"]
    assert "frontend:src/shared.py" in cards["src/shared.py"]["error"]
    assert "Target not found" in cards["src/nowhere.py"]["error"]
    assert 'search_codebase(query=..., repo="all")' in cards["src/nowhere.py"]["hint"]
    assert "_meta" in result


async def test_get_context_miss_keeps_the_default_repo_answer(routed_ws):
    from repowise.server.mcp_server import get_context

    plain = (await get_context(targets=["src/nowhere.py"], repo="backend"))["targets"]
    hinted = (await get_context(targets=["src/nowhere.py"]))["targets"]
    assert hinted["src/nowhere.py"].pop("hint")
    assert hinted == plain


# -- get_symbol ---------------------------------------------------------------


async def test_get_symbol_routes_repo_qualified_id(routed_ws):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("frontend:src/probe.py::frontend_probe")
    assert result["repo"] == "frontend"
    assert result["name"] == "frontend_probe"
    assert "frontend_probe" in result["source"]


async def test_get_symbol_routes_a_unique_path(routed_ws):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("src/only_fe.py:1-2")
    assert result["repo"] == "frontend"
    assert "only_fe" in json.dumps(result)


async def test_get_symbol_default_repo_unchanged(routed_ws):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("src/probe.py::backend_probe")
    assert "repo" not in result
    assert result["name"] == "backend_probe"


async def test_get_symbol_refuses_ambiguous_and_unknown(routed_ws):
    from repowise.server.mcp_server import get_symbol

    ambiguous = await get_symbol("src/shared.py::shared")
    assert ambiguous["candidates"] == ["frontend", "docs"]
    unknown = await get_symbol("src/nowhere.py::x")
    assert "error" in unknown
    assert 'search_codebase(query=..., repo="all")' in unknown["hint"]
    assert "repo" not in unknown


# -- get_risk -----------------------------------------------------------------


async def test_get_risk_routes_to_the_owning_repo(routed_ws):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(targets=["frontend:src/probe.py"])
    assert result["repo"] == "frontend"
    assert list(result["targets"]) == ["frontend:src/probe.py"]
    default = await get_risk(targets=["src/probe.py"])
    assert "repo" not in default


async def test_get_risk_refuses_a_cross_repo_batch(routed_ws):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(targets=["src/probe.py", "src/only_fe.py"])
    assert 'get_risk(targets=[\'src/probe.py\'], repo="backend")' in result["error"]
    assert 'get_risk(targets=[\'src/only_fe.py\'], repo="frontend")' in result["error"]
    assert result["routes"]["src/only_fe.py"] == {"repo": "frontend"}


async def test_get_risk_refuses_ambiguous_and_unknown(routed_ws):
    from repowise.server.mcp_server import get_risk

    ambiguous = await get_risk(targets=["src/shared.py"])
    assert ambiguous["routes"]["src/shared.py"] == {"candidates": ["frontend", "docs"]}
    unknown = await get_risk(targets=["src/nowhere.py"])
    assert "repo" not in unknown
    assert 'search_codebase(query=..., repo="all")' in unknown["targets"]["src/nowhere.py"]["hint"]


async def test_get_risk_pr_mode_keeps_deleted_files_with_their_change(routed_ws):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(changed_files=["src/only_fe.py", "src/deleted.py"])
    assert result["repo"] == "frontend"
    assert "error" not in result


async def test_get_risk_pr_mode_leaves_a_missing_target_on_the_default_repo(routed_ws):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(targets=["src/nowhere.py"], changed_files=["src/only_fe.py"])
    assert result["routes"]["src/nowhere.py"] == {"repo": "backend"}
    assert result["routes"]["src/only_fe.py"] == {"repo": "frontend"}


# -- get_health ---------------------------------------------------------------


async def test_get_health_routes_to_the_owning_repo(routed_ws):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["frontend:src/probe.py"])
    assert result["repo"] == "frontend"
    assert "frontend_probe_health" in json.dumps(result)
    assert "backend_probe_health" not in json.dumps(result)


async def test_get_health_default_repo_unchanged(routed_ws):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/probe.py"])
    assert "repo" not in result
    assert "backend_probe_health" in json.dumps(result)


async def test_get_health_refuses_cross_repo_and_ambiguous_hints_unknown(routed_ws):
    from repowise.server.mcp_server import get_health

    mixed = await get_health(targets=["src/probe.py", "src/only_fe.py"])
    assert "get_health(targets=" in mixed["error"]
    ambiguous = await get_health(targets=["src/shared.py"])
    assert ambiguous["routes"]["src/shared.py"] == {"candidates": ["frontend", "docs"]}
    unknown = await get_health(targets=["src/nowhere.py"])
    assert "repo" not in unknown
    assert "search_codebase" in unknown["unresolved"][0]["hint"]

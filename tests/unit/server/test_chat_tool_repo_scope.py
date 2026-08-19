"""Regression tests for issue #970 — chat tools in workspace mode.

Two things were broken when the HTTP server (``repowise serve``) ran over a
workspace:

1. Only the stdio MCP lifespan published a ``RepoRegistry`` to the tool
   globals, so every chat tool call took the single-repo branch of
   ``_resolve_repo_context`` and raised ``LookupError: Repository not
   found: <alias>``. ``set_tool_workspace`` now lets the HTTP lifespan
   publish the same state.
2. Nothing told the tools which repo the chat page was on, so the model's
   guess at the ``repo`` argument was the only input.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import repowise.server.mcp_server as mcp_mod
from repowise.server import chat_tools
from repowise.server.routers import chat


class _FakeRegistry:
    def __init__(self, aliases: list[str]) -> None:
        self._aliases = aliases

    def get_all_aliases(self) -> list[str]:
        return list(self._aliases)


@pytest.fixture
def workspace_registry():
    """Publish a fake workspace registry, restoring the globals afterwards."""
    previous = mcp_mod._registry
    chat_tools.set_tool_workspace(registry=_FakeRegistry(["boot", "gateway"]))
    yield
    chat_tools.set_tool_workspace(registry=previous)


@pytest.fixture
def captured_call(monkeypatch):
    """Replace get_overview with a recorder and return the recorded kwargs."""
    seen: dict[str, Any] = {}

    async def _fake_get_overview(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    tool_def = chat_tools.get_tool_registry()["get_overview"]
    monkeypatch.setattr(tool_def, "function", _fake_get_overview)
    return seen


@pytest.mark.asyncio
async def test_missing_repo_arg_is_filled_from_the_request(workspace_registry, captured_call):
    result = await chat_tools.execute_tool("get_overview", {}, repo="gateway")

    assert "error" not in result
    assert captured_call["repo"] == "gateway"


@pytest.mark.asyncio
async def test_unknown_repo_arg_is_replaced_by_the_request_repo(workspace_registry, captured_call):
    # The model only sees the repo *name* in the system prompt, so it can
    # produce a string that is not an alias at all.
    await chat_tools.execute_tool("get_overview", {"repo": "hpaddle-boot"}, repo="boot")

    assert captured_call["repo"] == "boot"


@pytest.mark.asyncio
async def test_valid_alias_from_the_model_is_kept(workspace_registry, captured_call):
    """Cross-repo questions must still be answerable from either page."""
    await chat_tools.execute_tool("get_overview", {"repo": "gateway"}, repo="boot")

    assert captured_call["repo"] == "gateway"


@pytest.mark.asyncio
async def test_repo_all_is_kept(workspace_registry, captured_call):
    await chat_tools.execute_tool("get_overview", {"repo": "all"}, repo="boot")

    assert captured_call["repo"] == "all"


@pytest.mark.asyncio
async def test_single_repo_mode_arguments_are_untouched(captured_call):
    """No registry means no aliases — leave the call exactly as the model made it."""
    previous = mcp_mod._registry
    chat_tools.set_tool_workspace(registry=None)
    try:
        await chat_tools.execute_tool("get_overview", {"repo": "whatever"}, repo="boot")
    finally:
        chat_tools.set_tool_workspace(registry=previous)

    assert captured_call["repo"] == "whatever"


@pytest.mark.asyncio
async def test_callers_arguments_are_not_mutated(workspace_registry, captured_call):
    arguments: dict[str, Any] = {"repo": "hpaddle-boot"}
    await chat_tools.execute_tool("get_overview", arguments, repo="boot")

    assert arguments == {"repo": "hpaddle-boot"}


class _Entry:
    def __init__(self, path: str, alias: str) -> None:
        self.path = path
        self.alias = alias


class _WsConfig:
    def __init__(self, repos: list[_Entry]) -> None:
        self.repos = repos


def _request_with_workspace(ws_root, repos):
    app = SimpleNamespace(
        state=SimpleNamespace(
            workspace_config=_WsConfig(repos),
            workspace_root=str(ws_root),
        )
    )
    return SimpleNamespace(app=app)


def test_workspace_alias_matches_on_path(tmp_path):
    request = _request_with_workspace(tmp_path, [_Entry("boot", "boot"), _Entry("gw", "gateway")])

    alias = chat._workspace_alias(request, str(tmp_path / "gw"), "gateway")

    assert alias == "gateway"


def test_workspace_alias_falls_back_to_the_repo_name(tmp_path, monkeypatch):
    """`repowise init .` stores local_path as ".", which resolves to the cwd."""
    request = _request_with_workspace(tmp_path, [_Entry("boot", "boot")])
    monkeypatch.chdir(tmp_path)

    assert chat._workspace_alias(request, ".", "boot") == "boot"


def test_workspace_alias_is_none_outside_a_workspace(tmp_path):
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(workspace_config=None, workspace_root=None))
    )

    assert chat._workspace_alias(request, str(tmp_path), "boot") is None


def test_workspace_alias_is_none_when_nothing_matches(tmp_path):
    request = _request_with_workspace(tmp_path, [_Entry("boot", "boot")])

    assert chat._workspace_alias(request, "/elsewhere/other", "other") is None


def test_set_tool_workspace_publishes_and_clears_state():
    previous = (mcp_mod._registry, mcp_mod._workspace_root, mcp_mod._cross_repo_enricher)
    registry = _FakeRegistry(["boot"])
    enricher = object()
    try:
        chat_tools.set_tool_workspace(
            registry=registry,
            workspace_root="/workspace",
            cross_repo_enricher=enricher,
        )
        assert mcp_mod._registry is registry
        assert mcp_mod._workspace_root == "/workspace"
        assert mcp_mod._cross_repo_enricher is enricher

        # Omitted arguments stay as they are.
        chat_tools.set_tool_workspace(workspace_root="/elsewhere")
        assert mcp_mod._registry is registry
        assert mcp_mod._workspace_root == "/elsewhere"

        chat_tools.set_tool_workspace(registry=None, workspace_root=None, cross_repo_enricher=None)
        assert mcp_mod._registry is None
        assert mcp_mod._workspace_root is None
        assert mcp_mod._cross_repo_enricher is None
    finally:
        (
            mcp_mod._registry,
            mcp_mod._workspace_root,
            mcp_mod._cross_repo_enricher,
        ) = previous

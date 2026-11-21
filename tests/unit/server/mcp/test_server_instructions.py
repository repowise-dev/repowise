"""The server ``instructions`` string decides whether an agent reaches for the tools.

Hosts that defer tool schemas show only tool names plus this string, and some
truncate long instructions, so the guidance that matters is pinned here.
"""

from __future__ import annotations

from typing import Any

import pytest

from repowise.server.mcp_server import mcp

# Well under the 2,000 chars some hosts keep.
_MAX_INSTRUCTIONS_CHARS = 1500
#: The base text before a repository or workspace line is added.
_BASE_INSTRUCTIONS_CHARS = 700


def test_instructions_fit_the_length_cap() -> None:
    assert len(mcp.instructions) < _MAX_INSTRUCTIONS_CHARS


def test_instructions_say_when_each_tool_replaces_a_text_search() -> None:
    text = mcp.instructions
    assert "search_codebase" in text and "`lines`" in text
    assert 'include=["references"]' in text
    assert "get_answer" in text and "per call" in text
    assert 'include=["files"]' in text
    assert "get_risk" in text and "get_change_risk" in text and "get_why" in text
    assert "repowise update" not in text


def test_instructions_stay_short() -> None:
    # Read into every session and paid again per registration; a response
    # already flags what it says about uncommitted edits, so that stays there.
    # Built here, not read from ``mcp.instructions``, which any earlier
    # create_mcp_server call in the process rewrites.
    from repowise.core.registry.instructions import server_instructions

    base = server_instructions(None)
    assert len(base) <= _BASE_INSTRUCTIONS_CHARS
    assert "uncommitted" not in base


def test_the_first_line_says_when_to_call() -> None:
    # A host that defers tools shows only this line until the model looks.
    from repowise.core.registry.instructions import server_instructions

    first = server_instructions(None).splitlines()[0]
    assert first.startswith("Call repowise first for where/how/why questions")
    assert server_instructions(None, brief=True, extra=["note"]) == first


def test_instructions_keep_the_no_index_and_workspace_guidance() -> None:
    # The boot text, sent before any selection, cannot know and keeps it.
    from repowise.core.registry.instructions import server_instructions

    text = server_instructions(None)
    assert "repowise init --yes" in text
    assert "do not run it yourself" in text


@pytest.mark.parametrize(("override", "named"), [(None, False), ("all", True)])
def test_workspace_tools_are_named_only_when_served(
    tmp_path: Any, monkeypatch: Any, override: str | None, named: bool
) -> None:
    # Both are opt-in, so a default workspace server must not advertise them.
    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server

    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: True)
    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_force_single_repo", _state._force_single_repo)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    try:
        server = create_mcp_server(str(tmp_path), tools=override)
        served = {t.name for t in server._tool_manager.list_tools()}
        for tool in ("get_architecture", "get_blast_radius"):
            assert (tool in served) is named
            assert (tool in server.instructions) is named
        assert len(server.instructions) < _MAX_INSTRUCTIONS_CHARS
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None


def test_handshake_reports_the_repowise_version_not_the_sdk_version() -> None:
    from repowise.server import __version__

    options = mcp._mcp_server.create_initialization_options()
    assert options.server_name == "repowise"
    assert options.server_version == __version__


@pytest.mark.parametrize(
    "override",
    [None, "all", "core", "lean", "-get_answer", "-get_why", "get_context,search_codebase"],
)
def test_instructions_name_no_tool_the_server_does_not_serve(
    tmp_path: Any, monkeypatch: Any, override: str | None
) -> None:
    # A host calls a tool it reads in the instructions even when it is not
    # listed, and the call fails.
    import re

    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server

    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_force_single_repo", _state._force_single_repo)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    try:
        server = create_mcp_server(str(tmp_path), tools=override)
        served = {t.name for t in server._tool_manager.list_tools()}
        named = {
            entry.name
            for entry in mcp_tool_registry.entries()
            if re.search(rf"\b{entry.name}\b", server.instructions)
        }
        assert named and named <= served
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None


def test_the_decisions_line_is_dropped_without_get_why() -> None:
    from repowise.core.registry.instructions import server_instructions

    line = "- 3 decisions govern files here: get_why(targets=[file]) first."
    assert line in server_instructions({"get_why"}, repo_line=line)
    assert "get_why" not in server_instructions({"get_answer", "get_risk"}, repo_line=line)


def _index(repo: Any, *, rows: int = 1) -> None:
    """A minimal store: the repositories table with *rows* rows."""
    import sqlite3

    (repo / ".repowise").mkdir(parents=True)
    conn = sqlite3.connect(repo / ".repowise" / "wiki.db")
    with conn:
        conn.execute("CREATE TABLE repositories (id TEXT PRIMARY KEY, local_path TEXT)")
        for n in range(rows):
            # A path from elsewhere: an index built in another directory still counts.
            conn.execute("INSERT INTO repositories VALUES (?, ?)", (str(n), "/elsewhere"))
    conn.close()


def _serve(monkeypatch: Any, repo: Any, cwd: Any) -> str:
    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server

    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_force_single_repo", _state._force_single_repo)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    monkeypatch.chdir(cwd)
    try:
        return create_mcp_server(str(repo), workspace_mode=False).instructions
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None


@pytest.mark.parametrize(("rows", "shown"), [(1, False), (0, True), (None, True)])
def test_the_no_index_line_goes_out_only_without_an_index(
    tmp_path: Any, monkeypatch: Any, rows: int | None, shown: bool
) -> None:
    from repowise.core.registry.instructions import NO_INDEX_LINE

    if rows is not None:
        _index(tmp_path, rows=rows)
    assert (NO_INDEX_LINE in _serve(monkeypatch, tmp_path, tmp_path)) is shown


def test_served_from_its_root_the_text_only_loses_the_no_index_line(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from repowise.core.registry.instructions import NO_INDEX_LINE

    before = _serve(monkeypatch, tmp_path, tmp_path).splitlines()
    _index(tmp_path)
    text = _serve(monkeypatch, tmp_path, tmp_path)
    assert NO_INDEX_LINE in before
    assert text.splitlines() == [line for line in before if line != NO_INDEX_LINE]
    sub = tmp_path / "pkg"
    sub.mkdir()
    assert _serve(monkeypatch, tmp_path, sub) == text


def test_served_from_outside_its_root_the_text_names_the_root(
    tmp_path: Any, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _index(repo)
    text = _serve(monkeypatch, repo, tmp_path)
    assert f"Paths in replies are relative to {repo.resolve()}." in text.splitlines()
    assert len(text) < _MAX_INSTRUCTIONS_CHARS


def test_a_worktree_served_from_its_main_checkout_is_its_own_root(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from pathlib import Path

    from repowise.server.mcp_server import _checkout
    from repowise.server.mcp_server._checkout import CallerCheckout

    main, worktree = tmp_path / "main", tmp_path / "wt"
    _index(main)
    worktree.mkdir()
    monkeypatch.setattr(_checkout, "_server_caller", CallerCheckout(Path(worktree), Path(main)))
    assert "Paths in replies" not in _serve(monkeypatch, main, worktree)


def test_a_workspace_has_an_index_when_any_member_does(tmp_path: Any, monkeypatch: Any) -> None:
    from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
    from repowise.server.mcp_server import _server

    config = WorkspaceConfig(
        repos=[RepoEntry(path="api", alias="api", is_primary=True), RepoEntry(path="web", alias="web")]
    )
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (tmp_path, config, "api"))
    assert _server._has_index(str(tmp_path), workspace=True) is False
    _index(tmp_path / "web")
    assert _server._has_index(str(tmp_path), workspace=True) is True

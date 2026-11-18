"""Guards shared by every unit test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def cursor_hooks_json(tmp_path_factory, monkeypatch):
    """Keep every test off the developer's real ``~/.cursor/hooks.json``.

    Rewrite-hook install paths run wherever ``~/.cursor`` exists, and not every
    suite isolates ``$HOME``. Tests that want Cursor present patch ``detect``.
    """
    from repowise.cli.agent_adapters import cursor

    hooks = tmp_path_factory.mktemp("cursor") / "hooks.json"
    monkeypatch.setattr(cursor, "user_hooks_path", lambda: hooks)
    monkeypatch.setattr(cursor.CursorAdapter, "detect", lambda self: False)
    return hooks


@pytest.fixture(autouse=True)
def network_switches_off(monkeypatch):
    """Start every test with the network off switches cleared.

    ``network_disabled()`` short-circuits the PyPI check and agent-CLI probes.
    A shell ``DO_NOT_TRACK`` or ``uninstall`` writing ``os.environ`` directly
    would otherwise turn those paths off for later tests. Tests of the gate set
    the switch themselves.
    """
    for var in ("REPOWISE_OFFLINE", "DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_signed_in_agent_cli(monkeypatch):
    """Keep the upgrade line off the developer's own signed-in agent CLIs.

    ``status``, ``doctor`` and the MCP keyless line probe for one; a test that
    wants a route patches ``agent_cli_auth`` in the core module itself. The MCP
    server's per-process probe and overview caches start empty per test.
    """
    from repowise.server.mcp_server import _meta

    monkeypatch.setattr(
        "repowise.core.providers.llm.subscription.agent_cli_auth", lambda _name: None
    )
    for name in ("_route_probes", "_routes", "_generate_calls", "_generate_counts"):
        monkeypatch.setattr(_meta, name, {})
    monkeypatch.setattr(_meta, "_overview_upgrade_named", set())


@pytest.fixture(autouse=True)
def fresh_test_map_cache():
    """In-memory stores reuse one URL and seed graph rows without moving the
    index stamp, so the process-wide index-state caches start empty per test."""
    from repowise.core.analysis.test_reachability import clear_test_map_cache
    from repowise.server.services.areas import clear_area_cache
    from repowise.server.services.coupling import clear_coupling_cache
    from repowise.server.services.graph_views import clear_code_graph_cache

    clear_test_map_cache()
    clear_area_cache()
    clear_coupling_cache()
    clear_code_graph_cache()
    yield
    clear_test_map_cache()
    clear_area_cache()
    clear_coupling_cache()
    clear_code_graph_cache()


@pytest.fixture
def isolated_run_mcp(monkeypatch):
    """For a test that calls ``run_mcp`` with ``mcp.run`` faked.

    The stdio path starts the parent watchdog, a daemon thread that outlives
    the test and exits the worker process if an ancestor dies, and reroutes
    the process's log sinks; both are replaced with no-ops. Everything else
    ``run_mcp`` changes on the shared server is put back at teardown: the
    ``_state`` repo fields, the selected tool surface and its instructions,
    and the network settings. A test that counts watchdog starts patches it
    again after this fixture.
    """
    from repowise.server.mcp_server import _server, _state, _tool_selection, ensure_full_surface

    monkeypatch.setattr(
        "repowise.server.mcp_server._watchdog.start_parent_watchdog", lambda: None
    )
    monkeypatch.setattr(
        "repowise.server.mcp_server._stdio_logging.route_logging_to_stderr", lambda: None
    )

    def keep(owner, *names):
        for name in names:
            monkeypatch.setattr(owner, name, getattr(owner, name))

    keep(_state, "_repo_path", "_force_single_repo")
    keep(_tool_selection, "_selected_surface", "_boot_inputs", "_always_load")
    keep(_server, "_client_instructions")
    mcp = ensure_full_surface()  # registered before the copy, so the snapshot stays whole
    keep(mcp._mcp_server, "instructions")
    keep(mcp.settings, "host", "port", "transport_security")
    # Selection trims the registered dict in place: the test gets a copy.
    monkeypatch.setattr(mcp._tool_manager, "_tools", dict(mcp._tool_manager._tools))


@pytest.fixture(scope="session")
def cobol_grammar():
    """Skip at run time when the COBOL grammar cannot load (first use may download it).

    A module-level skip runs during collection, where xdist workers racing on a
    cold grammar cache can disagree and abort the session (#5408)."""
    grammar_pack = pytest.importorskip(
        "tree_sitter_language_pack", reason="run `uv sync --all-packages`"
    )
    try:
        grammar_pack.get_language("cobol")
    except Exception as exc:  # pragma: no cover - depends on first-use grammar cache/network
        pytest.skip(f"COBOL grammar unavailable: {exc}")

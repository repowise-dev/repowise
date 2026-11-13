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

    clear_test_map_cache()
    clear_area_cache()
    clear_coupling_cache()
    yield
    clear_test_map_cache()
    clear_area_cache()
    clear_coupling_cache()


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

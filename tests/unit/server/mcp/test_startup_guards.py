"""Startup failures exit once with a named cause instead of respawning.

An MCP host restarts a stdio server that dies at startup. Two failures used
to escape as bare tracebacks and loop that way: an index directory the
process cannot create or open, and a tool module whose optional dependency
is missing. The first now raises a message naming the path and the fix; the
second is skipped so the rest of the surface still serves.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest

import repowise.server.mcp_server as mcp_pkg
from repowise.server.mcp_server import _server, _state
from repowise.server.mcp_server._server import StoreUnavailableError


@pytest.mark.asyncio
async def test_lifespan_names_the_path_when_the_store_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".repowise").write_text("not a directory\n")
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    async def _no_warm() -> None:
        return None

    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (None, None, None))

    with pytest.raises(StoreUnavailableError) as raised:
        async with _server._lifespan(_server.mcp):
            pass
    message = str(raised.value)
    assert str(tmp_path / ".repowise") in message
    assert "repowise init" in message


def test_missing_tool_dependency_skips_that_tool_and_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_import = importlib.import_module
    victim = f"{mcp_pkg.__name__}.tool_flows"

    def _import(name: str, package: str | None = None):
        if name == victim:
            raise ModuleNotFoundError("No module named 'lancedb'", name="lancedb")
        return real_import(name, package)

    monkeypatch.setattr(mcp_pkg, "_surface_applied", False)
    monkeypatch.setattr(importlib, "import_module", _import)

    with caplog.at_level(logging.WARNING, logger="repowise.mcp"):
        server = mcp_pkg.ensure_full_surface()

    assert server is _server.mcp
    record = next(r for r in caplog.records if "skipping tool module" in r.getMessage())
    assert "tool_flows" in record.getMessage()
    assert "lancedb" in record.getMessage()

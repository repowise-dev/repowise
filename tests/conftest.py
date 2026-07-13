"""Root test configuration — fixtures available to all test modules."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _no_telemetry_network():
    """Guarantee no test emits core-layer telemetry over the network.

    The MCP instrument seam emits an ``mcp_tool_call`` event via the core
    emitter's ``_post``; a test that drives the real wrapper with consent
    enabled would otherwise POST to the production ingest endpoint. Patch that
    sink to a no-op. Tests that assert emit behaviour re-patch it at function
    scope and still never touch the network.

    The CLI's ``command_run`` path is intentionally left alone: its tests patch
    ``PlatformClient.post`` at the class level, so patching the ``default_client``
    instance here would shadow those patches.
    """
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    try:
        from repowise.core.platform import telemetry as _core_telemetry

        mp.setattr(_core_telemetry, "_post", lambda envelope: None, raising=False)
    except Exception:
        pass
    yield
    mp.undo()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def sample_repo_path(repo_root: Path) -> Path:
    """Path to the multi-language sample repository used in integration tests."""
    path = repo_root / "tests" / "fixtures" / "sample_repo"
    assert path.exists(), (
        f"Sample repo not found at {path}. Run 'make install' to ensure test fixtures are in place."
    )
    return path


@pytest.fixture(scope="session")
def fixtures_dir(repo_root: Path) -> Path:
    """Path to the tests/fixtures/ directory."""
    return repo_root / "tests" / "fixtures"

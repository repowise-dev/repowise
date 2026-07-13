"""Root test configuration — fixtures available to all test modules."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _no_telemetry_network():
    """Guarantee no test ever emits real telemetry over the network.

    The MCP instrument seam and the CLI root wrapper emit anonymous events; a
    test that drives the real wrapper with consent enabled would otherwise POST
    to the production ingest endpoint. We patch the two senders (not just
    consent) so it can never happen regardless of a test's environment. Tests
    that assert emit behaviour re-patch these at function scope and still never
    touch the network.
    """
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    try:
        from repowise.core.platform import telemetry as _core_telemetry

        mp.setattr(_core_telemetry, "_post", lambda envelope: None, raising=False)
    except Exception:
        pass
    try:
        from repowise.cli.platform.client import default_client

        mp.setattr(default_client, "post", lambda *a, **k: True, raising=False)
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

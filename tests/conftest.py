"""Root test configuration — fixtures available to all test modules."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _no_telemetry_network(tmp_path_factory: pytest.TempPathFactory):
    """Guarantee no test emits telemetry over the network or into real state.

    The MCP instrument seam emits an ``mcp_tool_call`` event via the core
    emitter's ``_post``; a test that drives the real wrapper with consent
    enabled would otherwise POST to the production ingest endpoint. Patch that
    sink to a no-op. Tests that assert emit behaviour re-patch it at function
    scope and still never touch the network.

    The CLI's ``command_run`` path is not patched here — its tests patch
    ``PlatformClient.post`` at the class level, so patching the
    ``default_client`` instance would shadow those patches. Its delivery is
    already off under pytest (``emitter._under_test``); what this fixture adds
    is redirecting the event spool, so a test that records an event cannot
    leave it queued in the real ``~/.repowise`` for a later real invocation to
    deliver.
    """
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    try:
        from repowise.core.platform import telemetry as _core_telemetry

        mp.setattr(_core_telemetry, "_post", lambda envelope: None, raising=False)
    except Exception:
        pass
    try:
        from repowise.cli.platform.telemetry import spool as _cli_spool

        spool_path = tmp_path_factory.mktemp("telemetry") / _cli_spool.SPOOL_FILENAME
        mp.setattr(_cli_spool, "_path", lambda: spool_path)
    except Exception:
        pass
    yield
    mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _no_real_editor_setup():
    """Guarantee no test repoints the developer's real global editor config.

    ``repowise init`` (and ``doctor``'s self-heal path) defaults to
    ``--editor-setup`` on, which rewrites the *global*, machine-wide MCP
    server registration (e.g. ``~/.claude/settings.json``) to point at
    whatever repo path was just indexed. A test that drives the real CLI
    against a ``tmp_path`` fixture repo — e.g.
    ``tests/integration/test_cli.py``'s lock/watcher tests, which call
    ``init`` through ``CliRunner`` in-process rather than mocking it out —
    was doing exactly that: it repointed the developer's actual Claude Code
    MCP entry at a pytest temp directory that gets wiped on reboot, breaking
    every other project's ``repowise`` MCP tools until the *next*
    ``doctor``/``update`` run happened to self-heal it back (and even then,
    an already-running Claude Code session keeps using the stale spawn
    command it cached at connect time, since it has no reason to re-read
    the file mid-session).

    ``REPOWISE_SKIP_EDITOR_SETUP`` is the exact env var editor_setup.py and
    doctor's self-heal migrations already gate on — set once, for the whole
    session, so no test (present or future) can hit this by omission the
    way the lock/watcher tests did.
    """
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    mp.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate_structlog_config():
    """Restore structlog's global configuration after every test.

    ``configure_cli_logging`` / ``silence_logs_for_machine_output`` install a
    filtering bound logger at ERROR process-wide and never undo it, so the
    first test to exercise a CLI command silences every ``info`` and
    ``warning`` for the rest of the session.

    That is invisible until a test asserts on a log record.
    ``structlog.testing.capture_logs`` swaps the *processor chain*, not the
    *wrapper class*, so a filtering logger drops the event before ``LogCapture``
    ever runs and the test reads an empty list. Tests collected after
    ``tests/unit/cli`` therefore pass alone and fail in a full run.

    Snapshot and restore rather than reset to defaults: a test that configures
    structlog on purpose keeps working, and the next test still starts clean.
    """
    import structlog

    saved = structlog.get_config()
    try:
        yield
    finally:
        structlog.configure(**saved)


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

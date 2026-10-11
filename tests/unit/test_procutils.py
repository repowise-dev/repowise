"""Tests for repowise.core.procutils — cross-platform process probes.

These run real probes against the test process itself and short-lived
child processes, so they exercise the platform-specific code path of
whatever OS the suite runs on (kernel32 on Windows, /proc on Linux,
``ps`` on macOS).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from repowise.core.procutils import (
    ancestor_chain,
    parent_pid,
    pid_alive,
    process_create_token,
    process_name,
)


def _spawn_and_reap() -> int:
    """Run a trivial child to completion and return its (now dead) PID."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


# ---------------------------------------------------------------------------
# pid_alive
# ---------------------------------------------------------------------------


def test_pid_alive_self() -> None:
    assert pid_alive(os.getpid()) is True


def test_pid_alive_dead_child() -> None:
    dead_pid = _spawn_and_reap()
    assert pid_alive(dead_pid) is False


def test_pid_alive_rejects_garbage() -> None:
    assert pid_alive(0) is None
    assert pid_alive(-5) is None
    assert pid_alive("123") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# process_create_token
# ---------------------------------------------------------------------------


def test_create_token_self_is_stable() -> None:
    first = process_create_token(os.getpid())
    second = process_create_token(os.getpid())
    assert first is not None
    assert first == second


def test_create_token_dead_pid_is_none_or_differs_from_live() -> None:
    """A dead PID must never return the same token as a live process.

    (On most platforms it returns None; a racy PID reuse would return a
    *different* token — both are acceptable, equality would be a bug.)
    """
    dead_pid = _spawn_and_reap()
    token = process_create_token(dead_pid)
    assert token is None or token != process_create_token(os.getpid())


def test_create_token_distinguishes_processes() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        child_token = process_create_token(proc.pid)
        own_token = process_create_token(os.getpid())
        assert child_token is not None
        assert child_token != own_token
    finally:
        proc.kill()
        proc.wait(timeout=30)


# ---------------------------------------------------------------------------
# parent_pid / process_name / ancestor_chain
# ---------------------------------------------------------------------------


def test_parent_pid_of_child_is_us() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert parent_pid(proc.pid) == os.getpid()
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_process_name_of_child_is_python() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        name = process_name(proc.pid)
        assert name is not None
        assert "python" in name.lower()
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_ancestor_chain_starts_at_parent() -> None:
    if os.getppid() <= 1:
        pytest.skip("test process has no live parent to walk")
    chain = ancestor_chain()
    assert chain, "expected at least one resolvable ancestor"
    assert chain[0].pid == os.getppid()
    # Every entry carries a usable identity for the watchdog.
    for info in chain:
        assert info.pid > 1


def test_ancestor_chain_excludes_self_and_has_no_duplicates() -> None:
    chain = ancestor_chain()
    pids = [info.pid for info in chain]
    assert os.getpid() not in pids
    assert len(pids) == len(set(pids))

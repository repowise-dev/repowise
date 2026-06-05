"""MCP stdio parent-death watchdog.

The launcher-chain problem these tests pin down: console-script installs
run as ``client → repowise shim → python`` (three levels on Windows), so
``os.getppid()`` from the server is a shim that waits on *us* and never
dies when the client does. The watchdog walks past launcher-named
ancestors to the client and watches the whole recorded chain.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from repowise.core.procutils import ProcInfo, pid_alive, process_create_token
from repowise.server.mcp_server import _watchdog

# ---------------------------------------------------------------------------
# _is_launcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "python",
        "python3.12",
        "Python.exe",
        "pythonw.exe",
        "repowise",
        "repowise.exe",
        "uv",
        "uvx",
        # Shell wrappers (``cmd /c`` / ``sh -c`` spawn styles) — exact match.
        "cmd.exe",
        "sh",
        "bash",
        "powershell.exe",
        "pwsh",
        None,
    ],
)
def test_launcher_names(name: str | None) -> None:
    assert _watchdog._is_launcher(name) is True


@pytest.mark.parametrize(
    "name",
    # "sharepoint" pins exact (not prefix) matching for shell names.
    ["claude", "Claude.exe", "node", "Cursor.exe", "codex", "sharepoint", "explorer.exe"],
)
def test_client_names(name: str) -> None:
    assert _watchdog._is_launcher(name) is False


# ---------------------------------------------------------------------------
# compute_watch_set — stops at the first non-launcher ancestor
# ---------------------------------------------------------------------------


def test_watch_set_stops_at_client(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = [
        ProcInfo(pid=100, name="repowise.exe", create_token="a"),
        ProcInfo(pid=101, name="python.exe", create_token="b"),
        ProcInfo(pid=102, name="claude.exe", create_token="c"),
        # Above the client — must never be watched: it can die while the
        # client legitimately keeps running.
        ProcInfo(pid=103, name="powershell.exe", create_token="d"),
        ProcInfo(pid=104, name="explorer.exe", create_token="e"),
    ]
    monkeypatch.setattr(_watchdog, "ancestor_chain", lambda _pid: chain)

    watch = _watchdog.compute_watch_set()

    assert [w.pid for w in watch] == [100, 101, 102]


def test_watch_set_empty_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_watchdog, "ancestor_chain", lambda _pid: [])
    assert _watchdog.compute_watch_set() == []


def test_watch_set_all_launchers_keeps_whole_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = [
        ProcInfo(pid=100, name="python", create_token="a"),
        ProcInfo(pid=101, name="python3", create_token="b"),
    ]
    monkeypatch.setattr(_watchdog, "ancestor_chain", lambda _pid: chain)

    assert [w.pid for w in _watchdog.compute_watch_set()] == [100, 101]


# ---------------------------------------------------------------------------
# _ancestor_died
# ---------------------------------------------------------------------------


def test_dead_pid_is_died() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    info = ProcInfo(pid=proc.pid, name="python", create_token=None)
    assert _watchdog._ancestor_died(info) is True


def test_live_self_with_matching_token_is_not_died() -> None:
    info = ProcInfo(
        pid=os.getpid(), name="python", create_token=process_create_token(os.getpid())
    )
    assert _watchdog._ancestor_died(info) is False


def test_live_pid_with_mismatched_token_is_died() -> None:
    """PID recycled by an unrelated process counts as the ancestor dying."""
    info = ProcInfo(pid=os.getpid(), name="python", create_token="not-our-token")
    assert _watchdog._ancestor_died(info) is True


def test_unknown_liveness_is_not_died(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail open: probe uncertainty must never kill a live server."""
    monkeypatch.setattr(_watchdog, "pid_alive", lambda _pid: None)
    info = ProcInfo(pid=os.getpid(), name="python", create_token="whatever")
    assert _watchdog._ancestor_died(info) is False


# ---------------------------------------------------------------------------
# start_parent_watchdog
# ---------------------------------------------------------------------------


def test_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_watchdog._DISABLE_ENV, "1")
    assert _watchdog.start_parent_watchdog() is None


def test_inactive_when_chain_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_watchdog._DISABLE_ENV, raising=False)
    monkeypatch.setattr(_watchdog, "compute_watch_set", lambda: [])
    assert _watchdog.start_parent_watchdog() is None


# ---------------------------------------------------------------------------
# End-to-end: the orphan scenario
# ---------------------------------------------------------------------------

# The "server" loads _watchdog.py directly by file path (skipping the heavy
# mcp_server package import), starts the watchdog with a fast poll, signals
# readiness, then sleeps far past the test timeout. The 60s sleep doubles
# as cleanup if the watchdog ever fails to fire.
_SERVER_SCRIPT = textwrap.dedent(
    """
    import importlib.util, time
    spec = importlib.util.spec_from_file_location("rw_watchdog", {watchdog_path!r})
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._POLL_INTERVAL_SECONDS = 0.2
    thread = mod.start_parent_watchdog()
    assert thread is not None, "watchdog did not start"
    print("READY", flush=True)
    time.sleep(60)
    """
)

# The "client" spawns the server, waits until its watchdog is armed, hands
# the server PID to the test, and exits — orphaning the server exactly the
# way a crashed MCP client does.
_CLIENT_SCRIPT = textwrap.dedent(
    """
    import subprocess, sys
    proc = subprocess.Popen(
        [sys.executable, "-c", {server_script!r}],
        stdout=subprocess.PIPE, text=True,
    )
    line = proc.stdout.readline()
    assert line.strip() == "READY", f"unexpected: {{line!r}}"
    print(proc.pid, flush=True)
    """
)


def test_server_exits_when_client_dies() -> None:
    server_script = _SERVER_SCRIPT.format(watchdog_path=_watchdog.__file__)
    client_script = _CLIENT_SCRIPT.format(server_script=server_script)

    client = subprocess.run(
        [sys.executable, "-c", client_script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert client.returncode == 0, client.stderr
    server_pid = int(client.stdout.strip().splitlines()[-1])

    # The client is gone; the orphaned server must notice and exit.
    deadline = time.time() + 30
    while time.time() < deadline:
        if pid_alive(server_pid) is False:
            return
        time.sleep(0.25)
    pytest.fail(f"orphaned server (pid {server_pid}) did not exit within 30s")

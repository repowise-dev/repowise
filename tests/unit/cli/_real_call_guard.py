"""Make a real subprocess, network or socket call fail loudly in a test.

A patch on a name that stops intercepting (a module turned into a package,
a re-export moved) would otherwise run the real thing: bind a port, spawn
node, or hit GitHub. A test that patches one of these entries replaces the
guard, since its monkeypatch runs after this one.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import urllib.request

import httpx
import pytest


class UnexpectedRealCall(AssertionError):
    pass


def _refuse(what: str):
    def _raise(*args, **kwargs):
        raise UnexpectedRealCall(f"unexpected real call: {what} args={args!r}")

    return _raise


def _is_git(cmd) -> bool:
    argv0 = cmd if isinstance(cmd, str) else (cmd[0] if cmd else "")
    name = os.path.basename(str(argv0).split(" ", 1)[0]).lower()
    return name in ("git", "git.exe")


def guard_real_calls(monkeypatch: pytest.MonkeyPatch, *, allow_git: bool = False) -> None:
    """Refuse subprocesses, HTTP and non-ephemeral binds.

    ``allow_git`` lets git through for tests that build a real repository.
    Port-0 binds stay allowed: they are what ``socket.socketpair`` (the
    Windows asyncio loop) and the OS-assigned port fallback use, and they
    never claim a real port.
    """
    # The first scipy.sparse import (networkx pagerank) probes the CPU with lscpu on Linux.
    with contextlib.suppress(ImportError):
        import scipy.sparse  # noqa: F401

    real_popen_init = subprocess.Popen.__init__

    def _popen_init(self, args, *a, **kw):
        if allow_git and _is_git(args):
            return real_popen_init(self, args, *a, **kw)
        raise UnexpectedRealCall(f"unexpected real call: subprocess.Popen {args!r}")

    # Popen.__init__ also covers subprocess.run / check_output, which build one.
    monkeypatch.setattr(subprocess.Popen, "__init__", _popen_init)
    if not allow_git:
        monkeypatch.setattr(subprocess, "run", _refuse("subprocess.run"))
        monkeypatch.setattr(subprocess, "Popen", _refuse("subprocess.Popen"))

    monkeypatch.setattr(httpx, "stream", _refuse("httpx.stream"))
    monkeypatch.setattr(httpx.Client, "send", _refuse("httpx.Client.send"))
    monkeypatch.setattr(httpx.AsyncClient, "send", _refuse("httpx.AsyncClient.send"))
    monkeypatch.setattr(urllib.request, "urlopen", _refuse("urllib.request.urlopen"))

    real_bind = socket.socket.bind

    def _bind(self, address):
        if isinstance(address, tuple) and len(address) >= 2 and address[1] == 0:
            return real_bind(self, address)
        raise UnexpectedRealCall(f"unexpected real call: socket.bind {address!r}")

    monkeypatch.setattr(socket.socket, "bind", _bind)

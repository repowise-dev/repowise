"""Regression tests for Node.js version detection in `repowise serve` (issue #276).

When the user has an old Node.js on PATH, the bundled Next.js runtime crashes
with a cryptic syntax error (e.g. `Unexpected token '?'` on `??`) after the UI
appears to start. Detect the version up front and fall back to API-only mode
with a clear message instead.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from repowise.cli.commands import serve_cmd


class _FakeCompleted:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


def test_node_major_version_parses_standard_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted("v20.11.1\n")

    monkeypatch.setattr(serve_cmd.subprocess, "run", fake_run)
    assert serve_cmd._node_major_version("/usr/bin/node") == 20


def test_node_major_version_parses_old_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact scenario from issue #276 — Node 12 (pre-nullish-coalescing)."""

    def fake_run(*_args: Any, **_kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted("v12.22.12\n")

    monkeypatch.setattr(serve_cmd.subprocess, "run", fake_run)
    assert serve_cmd._node_major_version("/usr/bin/node") == 12


def test_node_major_version_handles_missing_v_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted("18.20.3\n")

    monkeypatch.setattr(serve_cmd.subprocess, "run", fake_run)
    assert serve_cmd._node_major_version("/usr/bin/node") == 18


def test_node_major_version_returns_none_on_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> _FakeCompleted:
        raise subprocess.CalledProcessError(1, "node")

    monkeypatch.setattr(serve_cmd.subprocess, "run", fake_run)
    assert serve_cmd._node_major_version("/usr/bin/node") is None


def test_node_major_version_returns_none_on_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> _FakeCompleted:
        raise OSError("not executable")

    monkeypatch.setattr(serve_cmd.subprocess, "run", fake_run)
    assert serve_cmd._node_major_version("/missing/node") is None


def test_node_major_version_returns_none_on_garbage_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted("not a version string\n")

    monkeypatch.setattr(serve_cmd.subprocess, "run", fake_run)
    assert serve_cmd._node_major_version("/usr/bin/node") is None


def test_minimum_node_major_matches_next_engines() -> None:
    """If this trips, packages/web/package.json `engines.node` bumped and the
    constant in serve_cmd needs to be updated to match."""
    assert serve_cmd._NODE_MIN_MAJOR == 20

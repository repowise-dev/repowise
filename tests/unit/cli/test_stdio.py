"""Regression tests for stdio UTF-8 reconfiguration (issue #271).

The bug: Windows shells default to cp1252 and `repowise init` crashes with
`UnicodeEncodeError: 'charmap' codec can't encode character '↳'` when Rich
tries to render progress sub-step glyphs. The CLI now reconfigures stdout
and stderr to UTF-8 with `errors="replace"` at import time so the legacy
Windows renderer never raises.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from repowise.cli import _stdio


class _RecordingStream:
    """Minimal stand-in for sys.stdout with a `reconfigure` method."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def reconfigure(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _NoReconfigureStream:
    """Stream without `reconfigure` (e.g. a swapped-in StringIO)."""

    def write(self, _data: str) -> int:
        return 0


class _RaisingStream:
    def reconfigure(self, **_kwargs: Any) -> None:
        raise OSError("stream is detached")


def test_reconfigure_sets_utf8_with_replace() -> None:
    stream = _RecordingStream()
    _stdio._reconfigure(stream)
    assert stream.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_reconfigure_tolerates_missing_method() -> None:
    """A StringIO has no `reconfigure` — must not raise."""
    _stdio._reconfigure(_NoReconfigureStream())
    _stdio._reconfigure(io.StringIO())


def test_reconfigure_swallows_oserror() -> None:
    """Detached or closed streams raise OSError — must be silent."""
    _stdio._reconfigure(_RaisingStream())


def test_reconfigure_tolerates_none_stream() -> None:
    _stdio._reconfigure(None)


def test_ensure_utf8_stdio_handles_swapped_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pytest -s` and `capsys` swap stdout for buffers without `reconfigure`.
    The hardening function must not raise in either case — otherwise importing
    the CLI under test would crash."""
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    _stdio._ensure_utf8_stdio()


def test_ensure_utf8_stdio_reconfigures_both_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _RecordingStream()
    err = _RecordingStream()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)

    _stdio._ensure_utf8_stdio()

    assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert err.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_replace_errors_actually_survives_cp1252_glyph() -> None:
    """End-to-end: a TextIOWrapper around a BytesIO using cp1252+replace
    must accept the `↳` glyph without raising — proving the chosen error
    handler is the right one."""
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="cp1252", errors="replace")
    wrapper.write("  ↳ betweenness centrality ✓\n")
    wrapper.flush()
    # The exact replacement char varies, but the important property is
    # no exception was raised and *some* bytes were written.
    assert raw.getvalue()

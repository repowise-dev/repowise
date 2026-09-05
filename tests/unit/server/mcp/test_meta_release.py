"""A newer release is named once per process in ``_meta``, never per call.

The stdio server was the one long-lived process that never checked its own
currency. The lifespan now polls the TTL-cached PyPI check off the loop
thread, and ``build_meta`` names a newer version in the first response after
the poller sees it, then stays quiet for that version.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.core.upgrade.release import ReleaseCheck
from repowise.server.mcp_server import _meta, _server, _state


@pytest.fixture(autouse=True)
def _clean_release_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_state, "_release_check", None)
    monkeypatch.setattr(_state, "_release_announced", None)


def _check(latest: str | None, current: str = "0.48.0") -> ReleaseCheck:
    available = None if latest is None else latest != current
    return ReleaseCheck(current_version=current, latest_version=latest, update_available=available)


def test_newer_release_is_named_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_state, "_release_check", _check("9.9.9"))
    first = _meta.build_meta()
    assert "9.9.9" in first["newer_release"]
    assert "0.48.0" in first["newer_release"]
    assert "newer_release" not in _meta.build_meta()


def test_current_or_unknown_release_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_state, "_release_check", _check("0.48.0"))
    assert "newer_release" not in _meta.build_meta()
    monkeypatch.setattr(_state, "_release_check", _check(None))
    assert "newer_release" not in _meta.build_meta()
    assert "newer_release" not in _meta.build_meta()


def test_a_later_newer_release_is_named_again_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_state, "_release_check", _check("9.9.9"))
    assert "newer_release" in _meta.build_meta()
    monkeypatch.setattr(_state, "_release_check", _check("10.0.0"))
    assert "10.0.0" in _meta.build_meta()["newer_release"]
    assert "newer_release" not in _meta.build_meta()


@pytest.mark.asyncio
async def test_poller_records_the_check_off_the_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_check(current_version: str, **_kw) -> ReleaseCheck:
        calls.append(current_version)
        return _check("9.9.9", current=current_version)

    monkeypatch.setattr(
        "repowise.core.upgrade.release.check_latest_version_cached", _fake_check
    )

    async def _stop_after_first(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(_server.asyncio, "sleep", _stop_after_first)
    with pytest.raises(asyncio.CancelledError):
        await _server._poll_release_check()
    from repowise.server import __version__

    assert calls == [__version__]
    assert _state._release_check.latest_version == "9.9.9"


@pytest.mark.asyncio
async def test_poller_survives_a_failing_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_current: str, **_kw) -> ReleaseCheck:
        raise RuntimeError("pypi down")

    monkeypatch.setattr("repowise.core.upgrade.release.check_latest_version_cached", _boom)

    async def _stop_after_first(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(_server.asyncio, "sleep", _stop_after_first)
    with pytest.raises(asyncio.CancelledError):
        await _server._poll_release_check()
    assert _state._release_check is None

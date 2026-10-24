"""Tests for the shared release-info layer in core (version compare, PyPI cache,
bundled changelog)."""

from __future__ import annotations

import json

import pytest

import repowise.core.upgrade.release as rel
from repowise.core.upgrade.release import (
    BUNDLED_CHANGELOG_PATH,
    ReleaseCheck,
    check_latest_version_cached,
    is_newer_version,
    load_bundled_changelog,
    parse_release,
)

# --- version compare ------------------------------------------------------


def test_is_newer_version():
    assert is_newer_version("0.22.0", "0.21.0")
    assert is_newer_version("0.21.2", "0.21")  # padded compare
    assert not is_newer_version("0.21.0", "0.21.0")
    assert not is_newer_version("0.20.0", "0.21.0")


def test_is_newer_version_unparsable_is_false():
    assert not is_newer_version("garbage", "0.21.0")
    assert not is_newer_version("0.21.0", "")


def test_parse_release_ignores_suffix():
    assert parse_release("0.15.2-rc1") == (0, 15, 2)
    assert parse_release("1.2") == (1, 2)
    assert parse_release("nope") is None


# --- bundled changelog ----------------------------------------------------


def test_bundled_changelog_exists_and_parses():
    assert BUNDLED_CHANGELOG_PATH.is_file()
    entries = load_bundled_changelog()
    assert len(entries) > 0
    assert all(e.version for e in entries)


# --- cached PyPI check ----------------------------------------------------


def _redirect_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(rel, "_CACHE_PATH", tmp_path / "update-check.json")


def test_check_latest_version_cached_miss_then_hit(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    calls = {"n": 0}

    def _fetch(timeout=2.0):
        calls["n"] += 1
        return "9.9.9", None

    monkeypatch.setattr(rel, "fetch_latest_version", _fetch)

    first = check_latest_version_cached("0.21.0")
    second = check_latest_version_cached("0.21.0")

    assert calls["n"] == 1  # second served from cache
    assert isinstance(first, ReleaseCheck)
    assert first.latest_version == "9.9.9"
    assert first.update_available is True
    assert second.update_available is True


def test_check_latest_version_cached_recomputes_against_current(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    (tmp_path / "update-check.json").write_text(
        json.dumps({"checked_at": rel.time.time(), "latest_version": "0.21.0", "error": None}),
        encoding="utf-8",
    )
    # Cache hit; latest == current -> no update available.
    result = check_latest_version_cached("0.21.0")
    assert result.update_available is False


def test_check_latest_version_unknown_when_fetch_fails(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: (None, "offline"))
    result = check_latest_version_cached("0.21.0")
    assert result.latest_version is None
    assert result.update_available is None
    assert result.error == "offline"


def test_expired_cache_refetches(monkeypatch, tmp_path):
    _redirect_cache(monkeypatch, tmp_path)
    (tmp_path / "update-check.json").write_text(
        json.dumps({"checked_at": 0, "latest_version": "1.0.0", "error": None}),
        encoding="utf-8",
    )
    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: ("9.9.9", None))
    result = check_latest_version_cached("0.21.0", ttl_hours=24)
    assert result.latest_version == "9.9.9"


def test_check_latest_version_cached_does_not_poison_on_failure(monkeypatch, tmp_path):
    """A transient fetch failure must not pin the cache to None for the TTL."""
    _redirect_cache(monkeypatch, tmp_path)
    calls = {"n": 0}

    def _fetch(timeout=2.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "offline"
        return "9.9.9", None

    monkeypatch.setattr(rel, "fetch_latest_version", _fetch)

    first = check_latest_version_cached("0.21.0", ttl_hours=1)
    assert first.latest_version is None
    assert first.update_available is None

    # Second call within TTL must retry (not serve cached None).
    second = check_latest_version_cached("0.21.0", ttl_hours=1)
    assert calls["n"] == 2
    assert second.latest_version == "9.9.9"
    assert second.update_available is True


# --- offline / opt-out switches -------------------------------------------


@pytest.mark.parametrize("var", ["REPOWISE_OFFLINE", "DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED"])
def test_network_off_switches_skip_release_fetches(monkeypatch, var):
    import httpx

    def _boom(*args, **kwargs):
        raise AssertionError("network call made with an off switch set")

    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setenv(var, "1")
    latest, error = rel.fetch_latest_version()
    assert latest is None and error
    assert rel.fetch_release_highlights("1.0.0") is None


def test_an_off_switch_leaves_no_empty_highlights_cache(monkeypatch, tmp_path):
    """Offline must not cache "no highlights", or they stay hidden once back online."""
    cache = tmp_path / "highlights.json"
    monkeypatch.setattr(rel, "_HIGHLIGHTS_CACHE_PATH", cache)
    monkeypatch.setenv("REPOWISE_OFFLINE", "1")

    assert rel.latest_highlights_cached("1.0.0") == []
    assert not cache.exists()


def test_do_not_track_zero_keeps_the_network_on(monkeypatch):
    from repowise.core.platform.telemetry import network_disabled

    for var in ("REPOWISE_OFFLINE", "REPOWISE_TELEMETRY_DISABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DO_NOT_TRACK", "0")

    assert network_disabled() is False

"""Notes of releases newer than the running one, read from the latest tag's
changelog for the upgrade banner's what's-new view."""

from __future__ import annotations

import json
import time

import httpx
import pytest

import repowise.core.upgrade.release as rel

TAGGED = """# Changelog

## [Unreleased]

### Added

- Not released yet.

## [0.58.0] - 2026-10-20

The newest release.

### Highlights

- **Charts in chat** · Any chart. → /repos/:id/chat

### Fixed

- A fix.

## [0.57.1] - 2026-10-17

### Fixed

- A patch.

## [0.57.0] - 2026-10-15

### Added

- The installed release.
"""


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _cache(monkeypatch, tmp_path):
    monkeypatch.setattr(rel, "_NEWER_NOTES_CACHE_PATH", tmp_path / "release-notes.json")


def test_fetch_reads_the_latest_tag_and_keeps_only_newer_releases(monkeypatch):
    urls: list[str] = []

    def _get(url, timeout):
        urls.append(url)
        return _Resp(TAGGED)

    monkeypatch.setattr(httpx, "get", _get)
    entries = rel.fetch_newer_changelog("0.57.0", "0.58.0")
    assert urls == [
        "https://raw.githubusercontent.com/repowise-dev/repowise/v0.58.0/docs/CHANGELOG.md"
    ]
    # The installed release and Unreleased are left out; anything between is kept.
    assert [e.version for e in entries] == ["0.58.0", "0.57.1"]
    assert entries[0].highlights[0].title == "Charts in chat"


def test_fetch_offline_is_none(monkeypatch):
    def _boom(url, timeout):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", _boom)
    assert rel.fetch_newer_changelog("0.57.0", "0.58.0") is None


def test_cached_hit_skips_the_network_and_round_trips(monkeypatch):
    calls = {"n": 0}

    def _get(url, timeout):
        calls["n"] += 1
        return _Resp(TAGGED)

    monkeypatch.setattr(httpx, "get", _get)
    first = rel.newer_changelog_cached("0.57.0", "0.58.0")
    second = rel.newer_changelog_cached("0.57.0", "0.58.0")
    assert calls["n"] == 1
    assert second == first  # entries survive the JSON cache intact
    # Either version moving is a miss.
    rel.newer_changelog_cached("0.57.1", "0.58.0")
    rel.newer_changelog_cached("0.57.0", "0.59.0")
    assert calls["n"] == 3


def test_failed_lookup_is_empty_and_retried_after_a_short_while(monkeypatch, tmp_path):
    monkeypatch.setattr(rel, "fetch_newer_changelog", lambda current, latest, timeout=2.0: None)
    assert rel.newer_changelog_cached("0.57.0", "0.58.0") == []

    cache = tmp_path / "release-notes.json"
    stale = json.loads(cache.read_text(encoding="utf-8"))
    stale["checked_at"] = time.time() - 3600
    cache.write_text(json.dumps(stale), encoding="utf-8")
    calls = {"n": 0}

    def _fetch(current, latest, timeout=2.0):
        calls["n"] += 1
        return rel.parse_changelog(TAGGED)[1:2]

    monkeypatch.setattr(rel, "fetch_newer_changelog", _fetch)
    assert [e.version for e in rel.newer_changelog_cached("0.57.0", "0.58.0")] == ["0.58.0"]
    assert calls["n"] == 1


def test_malformed_cache_refetches(monkeypatch, tmp_path):
    (tmp_path / "release-notes.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(httpx, "get", lambda url, timeout: _Resp(TAGGED))
    assert [e.version for e in rel.newer_changelog_cached("0.57.0", "0.58.0")] == [
        "0.58.0",
        "0.57.1",
    ]

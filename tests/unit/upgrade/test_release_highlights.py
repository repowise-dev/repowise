"""Highlights, release summary and Unreleased parsing, the newer-release
highlight fetch, and guards on the shipped changelog's highlights."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import repowise.core.upgrade.release as rel
from repowise.core.upgrade.changelog import (
    UNRELEASED,
    entries_between,
    parse_changelog,
    parse_highlight,
)

SAMPLE = """# Changelog

<!-- preamble comment -->

---

## [Unreleased]

The next release in one paragraph.

### Highlights

- **Charts in chat** · Any chart of your repo. → /repos/:id/chat

### Added

- Pending thing.

## [0.57.0] - 2026-10-20

First paragraph of the summary,
wrapped over two lines.

Second paragraph.

### Highlights

- **Charts in chat** · Ask for any chart. → /repos/:id/chat
- **One palette**: every chart shares one set of colours.
- **Settings models** - live lists -> /settings
- plain bullet with no bold title

### By the numbers

In the 3 days since 0.56.0:

- **205 pull requests** merged

### Added

- A feature.

---

## [0.56.0] - 2026-10-12

### Fixed

- A fix.
"""


def test_highlights_parse_separately_from_sections():
    e = parse_changelog(SAMPLE)[1]
    assert e.version == "0.57.0"
    assert [s.name for s in e.sections] == ["Added"]  # Highlights and numbers left out
    assert [h.title for h in e.highlights] == [
        "Charts in chat",
        "One palette",
        "Settings models",
        "plain bullet with no bold title",
    ]


def test_highlight_fields():
    h = parse_highlight("**Charts in chat** · Ask for any chart. → /repos/:id/chat")
    assert (h.id, h.title, h.blurb, h.href) == (
        "charts-in-chat",
        "Charts in chat",
        "Ask for any chart.",
        "/repos/:id/chat",
    )
    assert parse_highlight("**One palette**: every chart.").href is None
    assert parse_highlight("**One palette**: every chart.").blurb == "every chart."
    assert parse_highlight("**Settings models** - live lists -> /settings").href == "/settings"
    # A dash is a separator only with space on both sides.
    assert parse_highlight("**Fast** -fast path").blurb == "-fast path"
    assert parse_highlight("**Fast** — a path").blurb == "a path"


def test_highlight_id_is_stable_across_blurb_edits():
    a = parse_highlight("**Charts in chat!** · one")
    b = parse_highlight("**Charts in chat!** · two → /x")
    assert a.id == b.id == "charts-in-chat"


def test_summary_is_the_prose_before_the_first_section():
    entries = parse_changelog(SAMPLE)
    assert entries[1].summary == (
        "First paragraph of the summary, wrapped over two lines.\n\nSecond paragraph."
    )
    assert entries[2].summary == ""


def test_unreleased_parses_with_content():
    first = parse_changelog(SAMPLE)[0]
    assert first.version == UNRELEASED
    assert first.summary == "The next release in one paragraph."
    assert first.highlights[0].id == "charts-in-chat"


def test_empty_unreleased_is_dropped():
    text = "## [Unreleased]\n\n---\n\n## [1.0.0] - x\n### Added\n- real\n"
    assert [e.version for e in parse_changelog(text)] == ["1.0.0"]


def test_unreleased_is_never_within_a_running_version():
    entries = parse_changelog(SAMPLE)
    sel = entries_between(entries, newer_than="0.56.0", up_to="0.57.0")
    assert [e.version for e in sel] == ["0.57.0"]


# --- newer release highlights (banner) ------------------------------------


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(rel, "_HIGHLIGHTS_CACHE_PATH", tmp_path / "release-highlights.json")


def test_latest_highlights_cached_hit_skips_the_network(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    calls = {"n": 0}

    def _fetch(version, timeout=2.0):
        calls["n"] += 1
        return ["Charts in chat"]

    monkeypatch.setattr(rel, "fetch_release_highlights", _fetch)
    assert rel.latest_highlights_cached("0.57.0") == ["Charts in chat"]
    assert rel.latest_highlights_cached("0.57.0") == ["Charts in chat"]
    assert calls["n"] == 1
    # A different newer version is a miss.
    rel.latest_highlights_cached("0.58.0")
    assert calls["n"] == 2


def test_latest_highlights_offline_is_empty_and_cached(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    calls = {"n": 0}

    def _fetch(version, timeout=2.0):
        calls["n"] += 1
        return None

    monkeypatch.setattr(rel, "fetch_release_highlights", _fetch)
    assert rel.latest_highlights_cached("0.57.0") == []
    assert rel.latest_highlights_cached("0.57.0") == []
    assert calls["n"] == 1


def test_latest_highlights_empty_result_expires_sooner(monkeypatch, tmp_path):
    import time

    _redirect(monkeypatch, tmp_path)
    (tmp_path / "release-highlights.json").write_text(
        json.dumps({"checked_at": time.time() - 3600, "version": "0.57.0", "titles": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(rel, "fetch_release_highlights", lambda v, timeout=2.0: ["Now"])
    assert rel.latest_highlights_cached("0.57.0") == ["Now"]


def test_latest_highlights_expired_cache_refetches(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    (tmp_path / "release-highlights.json").write_text(
        json.dumps({"checked_at": 0, "version": "0.57.0", "titles": ["Old"]}), encoding="utf-8"
    )
    monkeypatch.setattr(rel, "fetch_release_highlights", lambda v, timeout=2.0: ["New"])
    assert rel.latest_highlights_cached("0.57.0") == ["New"]


def test_fetch_release_highlights_reads_that_version(monkeypatch):
    class _Resp:
        text = SAMPLE

        def raise_for_status(self):
            return None

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, timeout: _Resp())
    assert rel.fetch_release_highlights("0.57.0")[0] == "Charts in chat"
    assert rel.fetch_release_highlights("9.9.9") == []


def test_fetch_release_highlights_offline_is_none(monkeypatch):
    import httpx

    def _boom(url, timeout):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", _boom)
    assert rel.fetch_release_highlights("0.57.0") is None


def test_release_stats_missing_file_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(rel, "RELEASE_STATS_PATH", tmp_path / "nope.json")
    assert rel.load_release_stats() == {}


def test_bundled_release_stats_parse():
    stats = rel.load_release_stats()
    assert all(isinstance(v, dict) and "prs" in v for v in stats.values())


# --- guards on the shipped changelog --------------------------------------

_ROOT = Path(__file__).resolve().parents[3]
_APP = _ROOT / "packages" / "web" / "src" / "app"


def _route_exists(href: str) -> bool:
    """``/repos/:id/chat`` resolves to ``app/repos/[id]/chat/page.tsx``."""
    path = href.split("?")[0].split("#")[0].strip("/")
    parts = ["[id]" if p == ":id" else p for p in path.split("/") if p]
    return (_APP.joinpath(*parts) / "page.tsx").is_file()


def test_released_minor_versions_have_highlights():
    """From 0.57.0 every minor release (x.y.0) names what it brought."""
    missing = [
        e.version
        for e in rel.load_bundled_changelog()
        if e.version != UNRELEASED
        and e.release_tuple >= (0, 57, 0)
        and len(e.release_tuple) == 3
        and e.release_tuple[2] == 0
        and not e.highlights
    ]
    assert not missing, f"add a ### Highlights block (3-5 bullets) to: {missing}"


@pytest.mark.skipif(not _APP.is_dir(), reason="web app sources not present")
def test_highlight_routes_exist_in_the_web_app():
    bad = [
        (e.version, h.href)
        for e in rel.load_bundled_changelog()
        for h in e.highlights
        if h.href and not _route_exists(h.href)
    ]
    assert not bad, f"highlight routes with no page under packages/web/src/app: {bad}"

"""Tests for the /api/meta endpoints (version freshness + changelog)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import repowise.core.upgrade.release as rel


@pytest.fixture(autouse=True)
def _stub_pypi(monkeypatch, tmp_path):
    """Keep these tests offline and deterministic."""
    monkeypatch.setattr(rel, "_CACHE_PATH", tmp_path / "update-check.json")
    monkeypatch.setattr(rel, "_HIGHLIGHTS_CACHE_PATH", tmp_path / "release-highlights.json")
    monkeypatch.setattr(rel, "_NEWER_NOTES_CACHE_PATH", tmp_path / "release-notes.json")
    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: ("9.9.9", None))
    monkeypatch.setattr(
        rel, "fetch_release_highlights", lambda version, timeout=2.0: ["Charts in chat"]
    )


@pytest.mark.asyncio
async def test_version_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/meta/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_version"]
    assert body["latest_version"] == "9.9.9"
    assert body["update_available"] is True
    assert body["upgrade_command"]
    # No repo_id -> store fields stay unset.
    assert body["store_format_version"] is None
    assert body["reindex_recommended"] is False
    assert body["latest_highlights"] == ["Charts in chat"]
    assert isinstance(body["highlights"], list)


@pytest.mark.asyncio
async def test_version_endpoint_offline_highlights_keep_plain_banner(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(rel, "fetch_release_highlights", lambda version, timeout=2.0: None)
    body = (await client.get("/api/meta/version")).json()
    assert body["update_available"] is True
    assert body["latest_highlights"] == []


@pytest.mark.asyncio
async def test_version_endpoint_up_to_date(client: AsyncClient, monkeypatch) -> None:
    from repowise.server import __version__

    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: (__version__, None))
    resp = await client.get("/api/meta/version")
    assert resp.status_code == 200
    assert resp.json()["update_available"] is False
    assert resp.json()["latest_highlights"] == []


@pytest.mark.asyncio
async def test_version_endpoint_unknown_when_offline(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: (None, "offline"))
    resp = await client.get("/api/meta/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_version"] is None
    assert body["update_available"] is None


@pytest.mark.asyncio
async def test_version_endpoint_unknown_repo_id_is_graceful(client: AsyncClient) -> None:
    """An unresolvable repo_id must not error; store fields stay unset."""
    resp = await client.get("/api/meta/version", params={"repo_id": "does-not-exist"})
    assert resp.status_code == 200
    assert resp.json()["store_format_version"] is None


@pytest.mark.asyncio
async def test_version_endpoint_populates_store_status(client: AsyncClient, tmp_path) -> None:
    """A resolvable repo with a state.json fills the store-format fields."""
    import json

    from repowise.core.upgrade import STORE_FORMAT_VERSION
    from tests.unit.server.conftest import create_test_repo

    repo = await create_test_repo(client, tmp_path)
    repo_dir = tmp_path / "test-repo"
    (repo_dir / ".repowise").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".repowise" / "state.json").write_text(
        json.dumps({"store_format_version": STORE_FORMAT_VERSION, "written_by_version": "0.21.0"}),
        encoding="utf-8",
    )

    resp = await client.get("/api/meta/version", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["store_format_version"] == STORE_FORMAT_VERSION
    assert body["store_compatible"] is True
    assert body["reindex_recommended"] is False


@pytest.mark.asyncio
async def test_version_endpoint_flags_reindex_for_old_store(client: AsyncClient, tmp_path) -> None:
    """A store predating the concept tree reports reindex-recommended, not forced."""
    import json

    from tests.unit.server.conftest import create_test_repo

    repo = await create_test_repo(client, tmp_path)
    repo_dir = tmp_path / "test-repo"
    (repo_dir / ".repowise").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".repowise" / "state.json").write_text(
        json.dumps({"store_format_version": 1, "written_by_version": "0.21.0"}),
        encoding="utf-8",
    )

    resp = await client.get("/api/meta/version", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["store_format_version"] == 1
    assert body["store_compatible"] is False
    assert body["reindex_recommended"] is True
    assert body["reindex_command"]


@pytest.mark.asyncio
async def test_changelog_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/meta/changelog", params={"limit": 3})
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert 0 < len(entries) <= 3
    first = entries[0]
    assert first["version"]
    assert isinstance(first["sections"], list)


@pytest.mark.asyncio
async def test_changelog_endpoint_carries_summary_highlights_and_stats(
    client: AsyncClient, monkeypatch, tmp_path
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [1.2.0] - 2026-10-20\n\nThe summary.\n\n### Highlights\n\n"
        "- **Charts in chat** · Any chart. → /repos/:id/chat\n\n### Added\n\n- A thing.\n",
        encoding="utf-8",
    )
    stats = tmp_path / "release_stats.json"
    stats.write_text(
        '{"1.2.0": {"since": "1.1.0", "days": 4, "prs": 12, "contributors": 3,'
        ' "first_time_contributors": 1, "files_changed": 40, "lines_added": 900,'
        ' "lines_removed": 100, "types": {"fixes": 5, "features": 4},'
        ' "test_files_touched": 9, "test_files_new": 2, "issues_closed": 3}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(rel, "BUNDLED_CHANGELOG_PATH", changelog)
    monkeypatch.setattr(rel, "RELEASE_STATS_PATH", stats)

    entry = (await client.get("/api/meta/changelog")).json()["entries"][0]
    assert entry["summary"] == "The summary."
    assert entry["highlights"] == [
        {
            "id": "charts-in-chat",
            "title": "Charts in chat",
            "blurb": "Any chart.",
            "href": "/repos/:id/chat",
        }
    ]
    assert entry["stats"]["prs"] == 12
    assert entry["stats"]["types"]["perf"] == 0
    assert [s["name"] for s in entry["sections"]] == ["Added"]


@pytest.mark.asyncio
async def test_changelog_endpoint_drops_malformed_stats(
    client: AsyncClient, monkeypatch, tmp_path
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [1.2.0] - x\n\n### Added\n\n- A thing.\n", encoding="utf-8")
    stats = tmp_path / "release_stats.json"
    stats.write_text('{"1.2.0": {"prs": "many"}}', encoding="utf-8")
    monkeypatch.setattr(rel, "BUNDLED_CHANGELOG_PATH", changelog)
    monkeypatch.setattr(rel, "RELEASE_STATS_PATH", stats)

    entry = (await client.get("/api/meta/changelog")).json()["entries"][0]
    assert entry["stats"] is None


_NEWER = "## [9.9.9] - 2026-11-01\n\nThe advertised release.\n\n### Added\n\n- New.\n"


@pytest.mark.asyncio
async def test_changelog_include_newer_puts_the_advertised_release_on_top(
    client: AsyncClient, monkeypatch
) -> None:
    from repowise.core.upgrade.changelog import parse_changelog
    from repowise.server import __version__

    seen = {}

    def _fetch(current, latest, timeout=2.0):
        seen["args"] = (current, latest)
        return parse_changelog(_NEWER)

    monkeypatch.setattr(rel, "fetch_newer_changelog", _fetch)
    entries = (
        await client.get("/api/meta/changelog", params={"include_newer": True, "limit": 3})
    ).json()["entries"]
    assert seen["args"] == (__version__, "9.9.9")
    assert entries[0]["version"] == "9.9.9"
    assert entries[0]["summary"] == "The advertised release."
    assert len(entries) <= 3
    # The bundled entries follow.
    assert [e.version for e in rel.load_bundled_changelog()[:1]] == [entries[1]["version"]]


@pytest.mark.asyncio
async def test_changelog_include_newer_falls_back_to_bundled_when_offline(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(rel, "fetch_newer_changelog", lambda current, latest, timeout=2.0: None)
    with_newer = (await client.get("/api/meta/changelog", params={"include_newer": True})).json()[
        "entries"
    ]
    bundled = (await client.get("/api/meta/changelog")).json()["entries"]
    assert with_newer == bundled


@pytest.mark.asyncio
async def test_changelog_include_newer_up_to_date_does_not_fetch(
    client: AsyncClient, monkeypatch
) -> None:
    from repowise.server import __version__

    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: (__version__, None))

    def _fail(*args, **kwargs):
        raise AssertionError("no newer release to fetch")

    monkeypatch.setattr(rel, "fetch_newer_changelog", _fail)
    resp = await client.get("/api/meta/changelog", params={"include_newer": True})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_changelog_default_never_fetches(client: AsyncClient, monkeypatch) -> None:
    """The version footer's view: the installed release's notes, no network."""

    def _fail(*args, **kwargs):
        raise AssertionError("default changelog must not fetch")

    monkeypatch.setattr(rel, "fetch_newer_changelog", _fail)
    assert (await client.get("/api/meta/changelog")).status_code == 200

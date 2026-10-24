"""get_context on a directory: its git facts come from the files under it."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_directory_without_a_module_page_resolves_from_its_files(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/"], include=["ownership", "last_change"])
    card = result["targets"]["src/"]
    assert card["type"] == "module"
    # src/auth and src/db have module pages; src itself does not, and gets its own card.
    assert card["index_status"] == "directory_without_module_page"
    assert [f["path"] for f in card["docs"]["files"]] == [
        "src/auth/service.py",
        "src/db/models.py",
    ]
    # One hotspot file under it; a directory has no hotspot bit of its own.
    assert card["hotspot_files"] == 1
    assert "hotspot" not in card
    ownership = card["ownership"]
    assert ownership["files_with_history"] == 2
    assert ownership["owners_by_files"] == [
        {"name": "Alice", "files": 1},
        {"name": "Bob", "files": 1},
    ]
    # Per-file shares do not add up to a directory's, so none is claimed.
    assert not {"primary_owner", "owner_pct", "contributor_count", "bus_factor"} & set(ownership)
    last = card["last_change"]
    assert last["date"].startswith("2026-03-15")
    assert last["path"] == "src/auth/service.py"
    assert last["days_ago"] == 0
    # The fixture's authors carry no commit time, so no author is claimed.
    assert "author" not in last


@pytest.mark.asyncio
async def test_module_page_ownership_comes_from_its_files(setup_mcp):
    from repowise.server.mcp_server import get_context

    card = (await get_context(["src/db"], include=["ownership", "last_change"]))["targets"][
        "src/db"
    ]
    assert "index_status" not in card
    assert "hotspot_files" not in card and "hotspot" not in card
    assert card["ownership"]["owners_by_files"] == [{"name": "Bob", "files": 1}]
    assert card["last_change"]["date"].startswith("2025-09-01")
    assert card["last_change"]["path"] == "src/db/models.py"


@pytest.mark.asyncio
async def test_unknown_and_sibling_prefix_directories_stay_not_found(setup_mcp):
    from repowise.server.mcp_server import get_context

    targets = (await get_context(["src/nope", "src/au"]))["targets"]
    for t in ("src/nope", "src/au"):
        assert targets[t].get("type") != "module", (t, targets[t])


@pytest.mark.asyncio
async def test_directory_card_is_no_larger_than_a_file_card(setup_mcp):
    from repowise.server.mcp_server import get_context

    targets = (await get_context(["src/", "src/auth/service.py"]))["targets"]
    assert len(json.dumps(targets["src/"])) <= len(json.dumps(targets["src/auth/service.py"]))


def test_last_change_author_is_the_one_committing_at_that_moment():
    from repowise.server.mcp_server.tool_context.sections import _author_at

    moment = datetime(2026, 3, 15, tzinfo=UTC)
    authors = json.dumps(
        [
            {"name": "Alice", "commit_count": 9, "last_commit_ts": int(moment.timestamp()) - 60},
            {"name": "Bob", "commit_count": 1, "last_commit_ts": int(moment.timestamp())},
        ]
    )
    assert _author_at(authors, moment) == "Bob"
    tie = json.dumps(
        [{"name": n, "last_commit_ts": int(moment.timestamp())} for n in ("Alice", "Bob")]
    )
    assert _author_at(tie, moment) is None
    assert _author_at("[]", moment) is None
    assert _author_at("not json", moment) is None


async def _set_authors(session, path: str, authors: list[dict]) -> None:
    from sqlalchemy import update

    from repowise.core.persistence.models import GitMetadata

    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == path)
        .values(top_authors_json=json.dumps(authors))
    )
    await session.flush()


@pytest.mark.asyncio
async def test_file_last_change_names_who_made_it_not_the_owner(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    last = int(datetime(2026, 3, 15, tzinfo=UTC).timestamp())
    await _set_authors(
        session,
        "src/auth/service.py",
        [
            {"name": "Alice", "commit_count": 27, "last_commit_ts": last - 86400},
            {"name": "Bob", "commit_count": 15, "last_commit_ts": last},
        ],
    )
    card = (await get_context(["src/auth/service.py"], include=["ownership", "last_change"]))[
        "targets"
    ]["src/auth/service.py"]
    assert card["ownership"]["primary_owner"] == "Alice"
    assert card["last_change"]["author"] == "Bob"


@pytest.mark.asyncio
async def test_file_last_change_omits_an_author_no_stored_commit_matches(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    # The newest commit was by no stored author (a pure move, or past the cap).
    last = int(datetime(2026, 3, 15, tzinfo=UTC).timestamp())
    await _set_authors(
        session,
        "src/auth/service.py",
        [{"name": "Alice", "commit_count": 27, "last_commit_ts": last - 86400}],
    )
    result = await get_context(["src/auth/service.py"], include=["last_change"])
    last_change = result["targets"]["src/auth/service.py"]["last_change"]
    assert last_change["date"].startswith("2026-03-15")
    assert "author" not in last_change


@pytest.mark.asyncio
async def test_partial_module_match_is_case_sensitive(setup_mcp):
    from repowise.server.mcp_server import get_context

    targets = (await get_context(["SRC", "DB", "db"]))["targets"]
    # SQLite's LIKE ignores case; a module match must not.
    assert targets["SRC"].get("type") != "module", targets["SRC"]
    assert targets["DB"].get("type") != "module", targets["DB"]
    assert targets["db"]["type"] == "module"
    assert targets["db"]["docs"]["title"]

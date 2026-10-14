"""Change Records and attribution on the MCP surface.

``change:<id>`` targets and ``include=["changes", "provenance"]`` in get_context,
``include=["changes"]`` in get_risk and get_overview, and get_why citing the
change a commit landed in. Default responses stay byte-identical.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import update

from repowise.core import attribution
from repowise.core.persistence import crud
from repowise.core.persistence.models import GitMetadata

_FULL = {
    "abc1234": "abc1234" + "0" * 33,
    "def5678": "def5678" + "0" * 33,
}


@pytest.fixture(autouse=True)
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


def _row(change_id: str, day: int, files: list[str], **over) -> dict:
    sha = over.pop("sha", f"{day:02d}" + "b" * 38)
    number = change_id.removeprefix("pr-")
    row = {
        "change_id": change_id,
        "identity_kind": "pr",
        "identity_value": number,
        "shape": "squash",
        "forge": "github",
        "forge_ref": f"#{number}",
        "title": f"feat: change {number}",
        "merged_at": datetime(2026, 3, day, tzinfo=UTC),
        "head_sha": sha,
        "author_name": "Ann",
        "author_email": "ann@example.com",
        "commit_shas_json": json.dumps([sha]),
        "files_json": json.dumps([{"path": p, "added": 1, "deleted": 0} for p in files]),
        "files_changed": len(files),
        "lines_added": len(files),
        "lines_deleted": 0,
        "category": "feature",
        "provenance": "agent",
        "provenance_agent": 1,
        "provenance_known": 1,
        "metadata_source": "git",
    }
    row.update(over)
    return row


_ROWS = [
    _row("pr-1", 1, ["src/auth/service.py"], sha=_FULL["def5678"], title="Add JWT support"),
    _row(
        "pr-2",
        2,
        ["src/auth/service.py", "src/db/models.py"],
        category="fix",
        title="fix: token refresh",
        provenance="human_attested",
        provenance_agent=0,
    ),
    _row(
        "pr-3",
        3,
        ["src/auth/service.pyc"],  # a longer path never matches src/auth/service.py
        provenance="unknown",
        provenance_agent=0,
        provenance_known=0,
    ),
    _row(
        "pr-4",
        15,
        ["src/auth/service.py"],
        sha=_FULL["abc1234"],
        title="Merge pull request #4 from ann/auth",
        provenance=None,
        provenance_agent=None,
        provenance_known=None,
    ),
]


async def _seed(session, rows=_ROWS) -> None:
    await crud.upsert_changes(session, "repo1", rows)
    await session.commit()


def _without_meta(payload: dict) -> str:
    return json.dumps({k: v for k, v in payload.items() if k != "_meta"}, sort_keys=True)


def _attribution_off(root: Path) -> None:
    (root / ".repowise").mkdir(exist_ok=True)
    (root / ".repowise" / "config.yaml").write_text(
        "attribution:\n  enabled: false\n", encoding="utf-8"
    )


# -- get_context ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_target_card(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    result = await get_context(
        targets=["change:pr-2", "change:def5678", "change:pr-4", "change:pr-99"]
    )
    card = result["targets"]["change:pr-2"]
    assert card["type"] == "change"
    assert (card["id"], card["ref"], card["title"]) == ("pr-2", "#2", "fix: token refresh")
    assert card["merged_at"].startswith("2026-03-02")
    assert card["category"] == "fix"
    assert card["files"] == ["src/auth/service.py", "src/db/models.py"]
    assert card["provenance"] == {
        "label": "human_attested",
        "agent_commits": 0,
        "human_attested_commits": 1,
        "unknown_commits": 0,
        "coverage": 1.0,
    }
    # A commit sha resolves to the change that landed it.
    assert result["targets"]["change:def5678"]["id"] == "pr-1"
    assert result["targets"]["change:pr-4"]["provenance"] == {"unavailable": "not_recorded"}
    assert "No change 'pr-99'" in result["targets"]["change:pr-99"]["error"]
    assert "Ann" not in json.dumps(card)


@pytest.mark.asyncio
async def test_change_card_withholds_provenance_when_attribution_is_off(
    setup_mcp, session, tmp_path
):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    _attribution_off(tmp_path)
    card = (await get_context(targets=["change:pr-1"]))["targets"]["change:pr-1"]
    assert card["provenance"] == {"unavailable": "attribution_off"}


@pytest.mark.asyncio
async def test_file_card_lists_recent_changes_on_request(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    card = (await get_context(targets=["src/auth/service.py"], include=["changes"]))["targets"][
        "src/auth/service.py"
    ]
    rows = card["recent_changes"]
    assert [r["id"] for r in rows] == ["pr-4", "pr-2", "pr-1"]  # never pr-3's .pyc
    assert rows[1] == {
        "id": "pr-2",
        "ref": "#2",
        "title": "fix: token refresh",
        "merged_at": rows[1]["merged_at"],
        "category": "fix",
        "provenance": {"label": "human_attested", "coverage": 1.0},
    }
    assert "provenance" not in rows[0]  # stored without a label
    assert "recent_changes_total" not in card


@pytest.mark.asyncio
async def test_recent_changes_are_capped_with_their_total(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session, [_row(f"pr-{n}", n, ["src/db/models.py"]) for n in range(1, 9)])
    card = (await get_context(targets=["src/db/models.py"], include=["changes"]))["targets"][
        "src/db/models.py"
    ]
    assert [r["id"] for r in card["recent_changes"]] == ["pr-8", "pr-7", "pr-6", "pr-5", "pr-4"]
    assert card["recent_changes_total"] == 8


@pytest.mark.asyncio
async def test_file_provenance_carries_coverage_and_withholds_below_the_floor(
    setup_mcp, session, tmp_path
):
    from repowise.server.mcp_server import get_context

    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/auth/service.py")
        .values(agent_commit_count=6, attribution_known_count=21)
    )
    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/db/models.py")
        .values(agent_commit_count=1, attribution_known_count=2)
    )
    await session.commit()
    result = await get_context(
        targets=["src/auth/service.py", "src/db/models.py"], include=["provenance"]
    )
    covered = result["targets"]["src/auth/service.py"]["provenance"]
    assert covered["agent_commits"] == 6
    assert covered["human_attested_commits"] == 15
    assert covered["unknown_commits"] == 21
    assert covered["coverage"] == 0.5
    assert covered["agent_share"] == round(6 / 21, 4)
    withheld = result["targets"]["src/db/models.py"]["provenance"]
    assert withheld["agent_share"] is None
    assert withheld["suppressed_reason"] == "coverage_below_floor"
    assert withheld["unknown_commits"] == 13  # unknown is its own band

    _attribution_off(tmp_path)
    off = await get_context(targets=["src/auth/service.py"], include=["provenance"])
    assert off["targets"]["src/auth/service.py"]["provenance"] == {
        "unavailable": "attribution_off"
    }


def test_change_is_a_target_kind_not_a_repo_alias():
    from repowise.server.mcp_server._workspace_routing import _route_one

    route = _route_one("change:pr-1", {"change": Path("/x")}, "main", False)
    assert (route.repo, route.local, route.routed) == ("main", "change:pr-1", False)


# -- get_risk -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_lists_recent_changes_on_request(setup_mcp, session):
    from repowise.server.mcp_server import get_risk

    await _seed(session)
    result = await get_risk(targets=["src/db/models.py"], include=["changes"])
    card = result["targets"]["src/db/models.py"]
    assert [r["id"] for r in card["recent_changes"]] == ["pr-2"]
    assert "ignored_arguments" not in result


# -- get_overview ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_changes_by_category_week_and_agent_share(setup_mcp, session):
    from repowise.server.mcp_server import get_overview

    await _seed(session)
    block = (await get_overview(include=["changes"]))["changes"]
    assert block["total"] == 4
    assert block["window"]["days"] == 90
    assert block["window"]["until"].startswith("2026-03-15")
    assert block["by_category"] == {"feature": 3, "fix": 1}
    weeks = {w["week"]: w["changes"] for w in block["by_week"]}
    assert weeks["2026-02-23"] == 1 and weeks["2026-03-02"] == 2 and weeks["2026-03-09"] == 1
    assert sum(weeks.values()) == 4
    prov = block["provenance"]
    # Two of four changes have a known source: unknown never counts as human.
    assert (prov["agent_changes"], prov["human_attested_changes"], prov["unknown_changes"]) == (
        1,
        1,
        2,
    )
    assert prov["coverage"] == 0.5 and prov["agent_share"] == 0.5
    assert "covered changes" in prov["summary"]


@pytest.mark.asyncio
async def test_overview_agent_share_is_withheld_below_the_floor(setup_mcp, session):
    from repowise.server.mcp_server import get_overview

    rows = [_row(f"pr-{n}", n, ["src/x.py"], provenance="unknown", provenance_known=0) for n in range(1, 5)]
    rows.append(_row("pr-5", 5, ["src/x.py"]))
    await _seed(session, rows)
    prov = (await get_overview(include=["changes"]))["changes"]["provenance"]
    assert prov["coverage"] == 0.2
    assert prov["agent_share"] is None
    assert prov["suppressed_reason"] == "coverage_below_floor"


@pytest.mark.asyncio
async def test_overview_changes_without_records(setup_mcp):
    from repowise.server.mcp_server import get_overview

    assert (await get_overview(include=["changes"]))["changes"] == {"total": 0}


# -- get_why --------------------------------------------------------------------


def _commit_rows(value, out):
    if isinstance(value, dict):
        if isinstance(value.get("sha"), str) or isinstance(value.get("commit"), str):
            out.append(value)
        for key, child in value.items():
            if key != "evidence_refs":
                _commit_rows(child, out)
    elif isinstance(value, list):
        for child in value:
            _commit_rows(child, out)
    return out


@pytest.mark.asyncio
async def test_why_cites_the_change_a_commit_landed_in(setup_mcp, session):
    from repowise.server.mcp_server import get_why

    before = await get_why(query="src/auth/service.py")
    assert not any("change" in r for r in _commit_rows(before, []))
    await _seed(session)
    after = await get_why(query="src/auth/service.py")
    cited = {r["sha"]: r["change"] for r in _commit_rows(after, []) if "change" in r}
    # A squash commit's message is the change's title, so only the id and ref ride along.
    assert cited["def5678"] == {"id": "pr-1", "ref": "#1"}
    assert cited["abc1234"] == {
        "id": "pr-4",
        "ref": "#4",
        "title": "Merge pull request #4 from ann/auth",
    }


@pytest.mark.asyncio
async def test_why_cites_nothing_for_unnamed_changes(setup_mcp, session):
    from repowise.server.mcp_server import get_why

    before = _without_meta(await get_why(query="src/auth/service.py"))
    await _seed(
        session,
        [
            _row("commit-x", 1, ["src/auth/service.py"], sha=_FULL["def5678"], identity_kind="commit", forge_ref=None),
        ],
    )
    assert _without_meta(await get_why(query="src/auth/service.py")) == before


# -- defaults never grow ----------------------------------------------------------


@pytest.mark.asyncio
async def test_default_responses_are_byte_identical_with_change_records(setup_mcp, session):
    from repowise.server.mcp_server import get_context, get_overview, get_risk

    async def defaults() -> list[str]:
        return [
            _without_meta(await get_context(targets=["src/auth/service.py"])),
            _without_meta(await get_risk(targets=["src/auth/service.py"])),
            _without_meta(await get_overview()),
        ]

    before = await defaults()
    await _seed(session)
    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/auth/service.py")
        .values(attribution_known_count=21)
    )
    await session.commit()
    assert await defaults() == before

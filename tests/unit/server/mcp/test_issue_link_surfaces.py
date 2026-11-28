"""Decisions and change cards show the tracker issues stored for their commits.

With no stored link touching them, get_why and change cards are byte-identical.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import update

from repowise.core.persistence import crud
from repowise.core.persistence.models import DecisionRecord, IssueLink
from tests.unit.server.mcp.test_changes_mcp import _FULL, _row, _seed

_EVIDENCE = _FULL["abc1234"]


def _link(sha: str, key: str, kind: str = "bug", **over) -> IssueLink:
    fields = {
        "repository_id": "repo1",
        "commit_sha": sha,
        "issue_key": key,
        "source": "t",
        "issue_type": kind,
        "status": "Done",
        "title": f"{key} title",
        "url": f"https://tracker.example/{key}",
    }
    fields.update(over)
    return IssueLink(**fields)


def _dump(result: dict) -> str:
    return json.dumps({k: v for k, v in result.items() if k != "_meta"}, sort_keys=True)


async def _cite(session, *commits: str) -> None:
    await session.execute(
        update(DecisionRecord)
        .where(DecisionRecord.id == "dec1")
        .values(evidence_commits_json=json.dumps(list(commits)))
    )
    await session.commit()


def _dec1(result: dict) -> dict:
    rows = [*result.get("decisions", []), *result.get("candidates", [])]
    return next(r for r in rows if r["title"] == "Use JWT for authentication")


@pytest.mark.asyncio
async def test_get_why_is_unchanged_without_a_link_on_the_evidence(setup_mcp, session):
    from repowise.server.mcp_server import get_why

    await _cite(session, "abc1234")
    plain = await get_why("src/auth/service.py")
    session.add(_link("f" * 40, "ENG-1"))
    await session.commit()

    assert _dump(await get_why("src/auth/service.py")) == _dump(plain)
    assert "linked_issues" not in json.dumps(plain)


@pytest.mark.asyncio
async def test_get_why_lists_the_issues_its_evidence_commits_link_to(setup_mcp, session):
    from repowise.server.mcp_server import get_why

    await _cite(session, "abc1234")
    plain = _dec1(await get_why("src/auth/service.py"))
    for n in range(4):
        session.add(_link(_EVIDENCE, f"ENG-{n}", status="" if n else "Done"))
    await session.commit()

    row = _dec1(await get_why("src/auth/service.py"))

    assert row["linked_issues"][0] == {
        "key": "ENG-0",
        "type": "bug",
        "status": "Done",
        "title": "ENG-0 title",
        "url": "https://tracker.example/ENG-0",
    }
    assert "status" not in row["linked_issues"][1]
    assert len(row["linked_issues"]) == 3 and row["linked_issues_total"] == 4
    rest = {k: v for k, v in row.items() if k not in ("linked_issues", "linked_issues_total")}
    assert rest == plain


@pytest.mark.asyncio
async def test_change_card_is_unchanged_without_a_link(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    plain = await get_context(targets=["change:pr-1", "change:pr-2"])
    session.add(_link("f" * 40, "ENG-1"))
    await session.commit()

    assert _dump(await get_context(targets=["change:pr-1", "change:pr-2"])) == _dump(plain)


@pytest.mark.asyncio
async def test_change_card_quotes_the_stored_issue(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    session.add(_link(_FULL["def5678"], "ENG-7", "incident", title="Login loop"))
    await session.commit()

    card = (await get_context(targets=["change:pr-1"]))["targets"]["change:pr-1"]

    assert card["linked_issues"] == ["ENG-7 (incident, Done): Login loop"]


@pytest.mark.asyncio
async def test_change_card_enriches_a_git_key_and_keeps_an_unknown_one(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    row = _row("pr-9", 9, ["src/a.py"], linked_issues_json=json.dumps(["#4", "#5"]))
    await crud.upsert_changes(session, "repo1", [row])
    # Stored against the commit that closed it, not this change's.
    session.add(_link("a" * 40, "#4", "feature", status="Closed", title="Dark mode"))
    await session.commit()

    card = (await get_context(targets=["change:pr-9"]))["targets"]["change:pr-9"]

    assert card["linked_issues"] == ["#4 (feature, Closed): Dark mode", "#5"]

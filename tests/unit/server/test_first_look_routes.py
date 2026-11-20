"""/api/repos/{repo_id}/reports/first-look: the stored report, its prompts, staleness and a rewrite."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Repository
from repowise.core.report.first_look.model import (
    FirstLookReport,
    Item,
    Location,
    PromptRef,
    SectionBlock,
    Stamp,
    file_link,
    item_id,
)
from repowise.core.report.first_look.store import store_report, write_report_file
from repowise.server.routers import reports
from repowise.server.schemas.agent_prompts import AgentPromptResponse
from repowise.server.schemas.reports import FirstLookResponse
from tests.unit.server.conftest import create_test_repo

_ID = item_id("hygiene", "import_cycle", "src/a.py")


def _report(generator: str = "template") -> FirstLookReport:
    item = Item(
        id=_ID,
        section="hygiene",
        rule="import_cycle",
        title_facts={"files": 2},
        locations=(Location("src/a.py", 3), Location("src/b.py")),
        confidence="high",
        basis="inferred",
        severity="plan",
        effort="S",
        link=file_link("src/a.py", 3),
        prompt=PromptRef("first_look_item"),
        fingerprint="f1",
    )
    return FirstLookReport(
        stamp=Stamp("c1", None, "2026-11-20T00:00:00+00:00", generator=generator),
        repo={"name": "test-repo"},
        sections=(SectionBlock("security", ()), SectionBlock("hygiene", (item,))),
        top=(_ID,),
        prose={"writer": generator, "summary": "One cycle.", "items": {_ID: "Break the cycle."}},
    )


@pytest.fixture
async def reports_client(app, client: AsyncClient) -> AsyncClient:
    app.include_router(reports.router)
    return client


async def _stored_repo(client: AsyncClient, session_factory) -> dict:
    repo = await create_test_repo(client)
    async with get_session(session_factory) as session:
        await store_report(session, repo["id"], _report())
    return repo


@pytest.mark.anyio
async def test_the_stored_report_comes_back_with_staleness_and_its_prompt(
    reports_client: AsyncClient, session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(reports, "_provider_configured", lambda *a: False)
    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (None, None, None, "no key"))
    repo = await _stored_repo(reports_client, session_factory)

    resp = await reports_client.get(
        f"/api/repos/{repo['id']}/reports/first-look", params={"flavor": "cursor"}
    )

    assert resp.status_code == 200
    body = FirstLookResponse.model_validate(resp.json())
    assert body.report == _report().to_json()
    assert body.report["prose"]["items"] == {_ID: "Break the cycle."}
    assert body.flavor == "cursor"
    assert body.report_prompt.strip()
    # The empty index no longer has the cycle: the report's one item reads as fixed.
    assert body.staleness["fixed"] == [_ID]
    assert body.staleness["report_commit"] == "c1"
    assert body.can_rewrite is False  # no provider, no rewrite button
    assert body.rewrite_cost_usd is None

    class Provider:
        model_name = "claude-haiku-5-5"

    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (Provider(), "anthropic", "m", None))
    again = (await reports_client.get(f"/api/repos/{repo['id']}/reports/first-look")).json()
    assert again["can_rewrite"] is True
    assert again["rewrite_cost_usd"] is not None and again["rewrite_cost_usd"] >= 0


@pytest.mark.anyio
async def test_the_file_in_the_checkout_is_read_when_the_index_has_none(
    reports_client: AsyncClient,
) -> None:
    repo = await create_test_repo(reports_client)
    write_report_file(Path(repo["local_path"]), _report())

    resp = await reports_client.get(f"/api/repos/{repo['id']}/reports/first-look")

    assert resp.status_code == 200
    assert resp.json()["report"]["stamp"]["commit"] == "c1"


@pytest.mark.anyio
async def test_no_report_is_a_404_that_says_how_to_write_one(reports_client: AsyncClient) -> None:
    repo = await create_test_repo(reports_client)

    for path in ("", "/prompt", "/export.md"):
        resp = await reports_client.get(f"/api/repos/{repo['id']}/reports/first-look{path}")
        assert resp.status_code == 404
        assert "repowise init" in resp.json()["detail"]
    missing = await reports_client.get("/api/repos/nope/reports/first-look")
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_an_item_prompt_and_the_whole_report_prompt(
    reports_client: AsyncClient, session_factory
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    url = f"/api/repos/{repo['id']}/reports/first-look/prompt"

    one = AgentPromptResponse.model_validate(
        (await reports_client.get(url, params={"item": _ID, "flavor": "claude-code"})).json()
    )
    assert one.flavor == "claude-code"
    assert "src/a.py" in one.text

    whole = (await reports_client.get(url)).json()
    assert whole["flavor"] == "generic" and "src/a.py" in whole["text"]

    assert (await reports_client.get(url, params={"item": "fl_000000000000"})).status_code == 404


@pytest.mark.anyio
async def test_rewrite_runs_the_one_writer_with_the_repos_provider(
    reports_client: AsyncClient, session_factory, monkeypatch
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    calls: list[tuple] = []
    provider = object()

    async def fake_write(session, repository_id, repo_root, *, provider=None, settings=None, store=True):
        calls.append((repository_id, repo_root, provider))
        return _report(generator="claude-haiku-5-5")

    monkeypatch.setattr(reports, "write_first_look", fake_write)
    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (provider, "x", "m", None))

    resp = await reports_client.post(f"/api/repos/{repo['id']}/reports/first-look/rewrite")

    assert resp.status_code == 200
    assert resp.json()["report"]["stamp"]["generator"] == "claude-haiku-5-5"
    assert calls == [(repo["id"], Path(repo["local_path"]), provider)]


@pytest.mark.anyio
async def test_rewrite_without_a_provider_is_refused(
    reports_client: AsyncClient, session_factory, monkeypatch
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    url = f"/api/repos/{repo['id']}/reports/first-look/rewrite"

    async def fake_write(*a, **k):
        raise AssertionError("the writer must not run without a provider")

    monkeypatch.setattr(reports, "write_first_look", fake_write)
    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (None, None, None, "no key"))
    resp = await reports_client.post(url)
    assert resp.status_code == 409 and resp.json()["detail"] == "no key"


@pytest.mark.anyio
async def test_a_section_prompt_and_the_markdown_export(
    reports_client: AsyncClient, session_factory
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    base = f"/api/repos/{repo['id']}/reports/first-look"

    one = (await reports_client.get(f"{base}/prompt", params={"section": "hygiene"})).json()
    assert "src/a.py" in one["text"]
    assert (await reports_client.get(f"{base}/prompt", params={"section": "nope"})).status_code == 404

    md = await reports_client.get(f"{base}/export.md")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert 'filename="test-repo-report.md"' in md.headers["content-disposition"]
    assert "src/a.py" in md.text


@pytest.mark.anyio
async def test_an_item_and_a_section_together_is_refused_and_any_name_downloads(
    reports_client: AsyncClient, session_factory
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    base = f"/api/repos/{repo['id']}/reports/first-look"
    both = await reports_client.get(f"{base}/prompt", params={"item": _ID, "section": "hygiene"})
    assert both.status_code == 422

    async with get_session(session_factory) as session:
        row = await session.get(Repository, repo["id"])
        row.name = 'café "x"\r\n'
    md = await reports_client.get(f"{base}/export.md")
    assert md.status_code == 200
    assert md.headers["content-disposition"] == 'attachment; filename="caf---x----report.md"'

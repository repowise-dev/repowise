"""/api/repos/{repo_id}/reports/first-look: the living report, its prompts, marks, exports and a rewrite."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
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
from repowise.core.report.first_look.store import store_report
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
async def test_the_report_now_is_measured_against_the_first_look(
    reports_client: AsyncClient, session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (None, None, None, "no key"))
    monkeypatch.setattr(reports, "_provider_configured", lambda *a: False)
    repo = await _stored_repo(reports_client, session_factory)

    resp = await reports_client.get(
        f"/api/repos/{repo['id']}/reports/first-look", params={"flavor": "cursor"}
    )

    assert resp.status_code == 200
    body = FirstLookResponse.model_validate(resp.json())
    assert body.flavor == "cursor"
    assert body.report_prompt.strip()
    # The empty index no longer has the cycle and nobody answered it: it reads as resolved.
    baseline = body.report["baseline"]
    assert baseline["stamp"]["commit"] == "c1"
    (gone,) = baseline["resolved"]
    assert gone["id"] == _ID and gone["status"] == "resolved" and gone["text"] == "Break the cycle."
    assert all(i["id"] != _ID for s in body.report["sections"] for i in s["items"])
    assert body.can_rewrite is False and body.rewrite_cost_usd is None

    class Provider:
        model_name = "claude-haiku-5-5"

    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (Provider(), "anthropic", "m", None))
    again = (await reports_client.get(f"/api/repos/{repo['id']}/reports/first-look")).json()
    assert again["can_rewrite"] is True
    assert again["rewrite_cost_usd"] is not None and again["rewrite_cost_usd"] >= 0


@pytest.mark.anyio
async def test_a_repository_with_no_stored_report_still_reads_without_writing_one(
    reports_client: AsyncClient,
) -> None:
    repo = await create_test_repo(reports_client)

    resp = await reports_client.get(f"/api/repos/{repo['id']}/reports/first-look")

    assert resp.status_code == 200
    assert resp.json()["report"]["baseline"] == {"stamp": None, "resolved": []}
    assert not (Path(repo["local_path"]) / ".repowise" / "reports").exists()
    assert (await reports_client.get("/api/repos/nope/reports/first-look")).status_code == 404


@pytest.mark.anyio
async def test_prompts_for_an_item_a_section_and_the_whole_report(
    reports_client: AsyncClient, session_factory
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    url = f"/api/repos/{repo['id']}/reports/first-look/prompt"

    one = AgentPromptResponse.model_validate(
        (await reports_client.get(url, params={"item": _ID, "flavor": "claude-code"})).json()
    )
    assert one.flavor == "claude-code" and "src/a.py" in one.text
    assert (await reports_client.get(url, params={"item": "fl_000000000000"})).status_code == 404

    whole = (await reports_client.get(url)).json()
    assert whole["flavor"] == "generic" and whole["text"].strip()
    assert (await reports_client.get(url, params={"section": "nope"})).status_code == 404
    both = await reports_client.get(url, params={"item": _ID, "section": "hygiene"})
    assert both.status_code == 422


@pytest.mark.anyio
async def test_a_mark_goes_through_the_one_marker_and_its_errors_read_plainly(
    reports_client: AsyncClient, session_factory, monkeypatch
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    url = f"/api/repos/{repo['id']}/reports/first-look/items"

    unknown = await reports_client.put(f"{url}/fl_000000000000/mark", json={"status": "done"})
    assert unknown.status_code == 404 and "No repo report item" in unknown.json()["detail"]
    no_reason = await reports_client.put(f"{url}/{_ID}/mark", json={"status": "ack"})
    assert no_reason.status_code == 422 and "reason" in no_reason.json()["detail"]
    bad = await reports_client.put(f"{url}/{_ID}/mark", json={"status": "later"})
    assert bad.status_code == 422

    calls: list[tuple] = []

    async def fake_mark(
        session, repository_id, item_id, status, *, reason=None, days=None, repo_root=None
    ):
        calls.append((repository_id, item_id, status, reason, days))
        return {"id": item_id, "status": "snoozed", "store": "actions", "until": "2026-12-04T00:00:00+00:00"}

    monkeypatch.setattr(reports, "mark_item", fake_mark)
    ok = await reports_client.put(f"{url}/{_ID}/mark", json={"status": "snooze", "days": 14})
    assert ok.status_code == 200 and ok.json()["status"] == "snoozed"
    assert calls == [(repo["id"], _ID, "snooze", None, 14)]


@pytest.mark.anyio
async def test_the_markdown_export_downloads_as_the_repo_report_with_no_prompts(
    reports_client: AsyncClient, session_factory
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    url = f"/api/repos/{repo['id']}/reports/first-look/export.md"

    md = await reports_client.get(url)
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.headers["content-disposition"] == 'attachment; filename="repo-report.md"'
    assert "<details>" not in md.text



@pytest.mark.anyio
async def test_rewrite_runs_the_one_writer_with_the_repos_provider(
    reports_client: AsyncClient, session_factory, monkeypatch
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    url = f"/api/repos/{repo['id']}/reports/first-look/rewrite"
    calls: list[tuple] = []
    provider = object()

    async def fake_write(session, repository_id, repo_root, *, provider=None, settings=None, store=True):
        calls.append((repository_id, repo_root, provider))
        return _report(generator="claude-haiku-5-5")

    monkeypatch.setattr(reports, "write_first_look", fake_write)
    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (None, None, None, "no key"))
    refused = await reports_client.post(url)
    assert refused.status_code == 409 and refused.json()["detail"] == "no key"
    assert calls == []

    monkeypatch.setattr(reports, "resolve_provider", lambda *a, **k: (provider, None, "m", None))
    resp = await reports_client.post(url)
    assert resp.status_code == 200
    assert calls == [(repo["id"], Path(repo["local_path"]), provider)]


@pytest.mark.anyio
async def test_a_mark_from_the_page_is_what_the_next_read_shows(
    reports_client: AsyncClient, session_factory
) -> None:
    repo = await _stored_repo(reports_client, session_factory)
    base = f"/api/repos/{repo['id']}/reports/first-look"
    before = (await reports_client.get(base)).json()["report"]
    assert before["baseline"]["resolved"][0]["status"] == "resolved"

    done = await reports_client.put(f"{base}/items/{_ID}/mark", json={"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done"

    # A person's answer holds the item in place instead of letting it resolve.
    after = (await reports_client.get(base)).json()["report"]
    (held,) = [i for s in after["sections"] for i in s["items"] if i["id"] == _ID]
    assert held["status"] == "done"
    assert after["baseline"]["resolved"] == []

    days_wrong = await reports_client.put(f"{base}/items/{_ID}/mark", json={"status": "done", "days": 3})
    assert days_wrong.status_code == 422
    too_long = await reports_client.put(
        f"{base}/items/{_ID}/mark", json={"status": "ack", "reason": "x" * 501}
    )
    assert too_long.status_code == 422
    assert (await reports_client.put("/api/repos/nope/reports/first-look/items/x/mark", json={"status": "done"})).status_code == 404

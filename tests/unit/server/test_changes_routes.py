"""GET /api/repos/{id}/changes and /changes/{change_id}."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core import attribution
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


@pytest.fixture(autouse=True)
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


def _row(change_id: str, day: int, **over) -> dict:
    row = {
        "change_id": change_id,
        "identity_kind": "pr",
        "identity_value": change_id.removeprefix("pr-"),
        "shape": "squash",
        "forge": "github",
        "forge_ref": "#" + change_id.removeprefix("pr-"),
        "title": f"feat: change {change_id}",
        "merged_at": datetime(2026, 1, day, tzinfo=UTC),
        "head_sha": f"{day:02d}" + "a" * 38,
        "author_name": "Ann",
        "author_email": "ann@example.com",
        "commit_shas_json": json.dumps([f"{day:02d}" + "a" * 38]),
        "files_json": json.dumps([{"path": "src/x.py", "added": 2, "deleted": 0}]),
        "files_changed": 1,
        "lines_added": 2,
        "lines_deleted": 0,
        "category": "feature",
        "provenance": "agent",
        "provenance_agent": 1,
        "provenance_known": 1,
        "metadata_source": "git",
    }
    row.update(over)
    return row


async def _seed(app, repo_id: str) -> None:
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_changes(
            session,
            repo_id,
            [
                _row("pr-1", 1),
                _row("pr-2", 2, category="fix", title="fix: crash"),
                _row("pr-3", 3, files_json=json.dumps([{"path": "docs/a.md", "added": 1, "deleted": 0}])),
            ],
        )


@pytest.mark.asyncio
async def test_list_is_newest_first_and_filters(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    url = f"/api/repos/{repo['id']}/changes"

    page = (await client.get(url)).json()
    assert page["total"] == 3 and page["attribution"] is True
    assert [c["id"] for c in page["items"]] == ["pr-3", "pr-2", "pr-1"]
    first = page["items"][0]
    assert first["schema_version"] == 1 and first["commit_count"] == 1
    assert "files" not in first
    assert first["provenance"]["label"] == "agent"

    assert [c["id"] for c in (await client.get(url, params={"category": "fix"})).json()["items"]] == [
        "pr-2"
    ]
    assert [c["id"] for c in (await client.get(url, params={"q": "docs/a"})).json()["items"]] == [
        "pr-3"
    ]


@pytest.mark.asyncio
async def test_show_by_id_or_sha(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}/changes"

    record = (await client.get(f"{base}/pr-2")).json()
    assert record["files"] == [{"path": "src/x.py", "added": 2, "deleted": 0}]
    assert record["metadata"]["source"] == "git" and record["metadata"]["reviewers"] is None
    assert (await client.get(f"{base}/{'02' + 'a' * 8}")).json()["id"] == "pr-2"
    assert (await client.get(f"{base}/pr-99")).status_code == 404
    assert (await client.get("/api/repos/nope/changes")).status_code == 404


@pytest.mark.asyncio
async def test_attribution_off_hides_provenance(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    config = Path(repo["local_path"]) / ".repowise" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("attribution:\n  enabled: false\n", encoding="utf-8")

    page = (await client.get(f"/api/repos/{repo['id']}/changes")).json()
    assert page["attribution"] is False
    assert all(c["provenance"] is None for c in page["items"])
    assert (await client.get(f"/api/repos/{repo['id']}/changes/pr-1")).json()["provenance"] is None


def _fake_build(monkeypatch, seen: list[str], gate=None):
    import asyncio

    from repowise.core.changes.evidence import ChangeEvidence, EvidenceItem
    from repowise.server.routers import changes as route

    async def _build(session, repo_id, root, row):
        seen.append(row.change_id)
        if gate is not None:
            await gate.wait()
        await asyncio.sleep(0)
        return ChangeEvidence(
            change_id=row.change_id,
            revspec=row.head_sha,
            generated_at="2026-10-14T00:00:00+00:00",
            items=[
                EvidenceItem(
                    "health_delta", "Health delta", "ok", "measured", {"introduced": 0}, [], "change health"
                )
            ],
        )

    async def _current(_root, _evidence):
        return False

    monkeypatch.setattr(route, "build_stored_evidence", _build)
    # These tests are about the checklist build; outcomes stay as built.
    monkeypatch.setattr(route, "outcomes_stale", _current)
    return route


@pytest.mark.asyncio
async def test_detail_carries_evidence_only_when_asked_and_keeps_it(
    client: AsyncClient, app, monkeypatch
) -> None:
    seen: list[str] = []
    _fake_build(monkeypatch, seen)
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}/changes/pr-1"

    plain = (await client.get(base)).json()
    assert plain["evidence"] is None and plain["evidence_building"] is False and seen == []
    record = (await client.get(base, params={"evidence": "true"})).json()
    evidence = record["evidence"]
    assert evidence["schema_version"] == 1 and evidence["change_id"] == "pr-1"
    assert evidence["items"][0] == {
        "kind": "health_delta",
        "label": "Health delta",
        "status": "ok",
        "basis": "measured",
        "value": {"introduced": 0},
        "details": [],
        "source": "change health",
        # Worded by the client from a key and its numbers.
        "line": {
            "key": "health_delta_partial",
            "params": {"introduced": 0, "worsened": 0, "resolved": 0},
        },
    }
    # Kept: the next read builds nothing; refresh builds again.
    again = (await client.get(base, params={"evidence": "true"})).json()
    assert again["evidence"] == evidence and seen == ["pr-1"]
    await client.get(base, params={"evidence": "true", "refresh": "true"})
    assert seen == ["pr-1", "pr-1"]


@pytest.mark.asyncio
async def test_a_slow_build_answers_building_and_runs_once(client: AsyncClient, app, monkeypatch) -> None:
    import asyncio

    seen: list[str] = []
    gate = asyncio.Event()
    route = _fake_build(monkeypatch, seen, gate)
    monkeypatch.setattr(route, "EVIDENCE_WAIT_SECONDS", 0.05)
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}/changes/pr-1"

    first, second = await asyncio.gather(
        client.get(base, params={"evidence": "true"}),
        client.get(base, params={"evidence": "true"}),
    )
    for reply in (first.json(), second.json()):
        assert reply["evidence"] is None and reply["evidence_building"] is True
    assert seen == ["pr-1"]
    gate.set()
    for _ in range(100):
        if not route._BUILDS:
            break
        await asyncio.sleep(0.01)
    done = (await client.get(base, params={"evidence": "true"})).json()
    assert done["evidence"]["change_id"] == "pr-1" and seen == ["pr-1"]


def _evidence(change_id: str, *statuses: str, details: list | None = None):
    from repowise.core.changes.evidence import ChangeEvidence, EvidenceItem

    return ChangeEvidence(
        change_id=change_id,
        revspec=None,
        generated_at="2026-10-14T00:00:00+00:00",
        items=[
            EvidenceItem(f"kind{i}", "Item", s, "measured", None, details or [], "test")
            for i, s in enumerate(statuses)
        ],
    )


def _checklist(change_id: str, *statuses: str, details: list | None = None) -> str:
    return crud.dump_evidence(_evidence(change_id, *statuses, details=details).to_dict())


@pytest.mark.asyncio
async def test_list_evidence_summary_is_opt_in_and_never_builds(
    client: AsyncClient, app, monkeypatch
) -> None:
    seen: list[str] = []
    _fake_build(monkeypatch, seen)
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_changes(
            session,
            repo["id"],
            [
                _row("pr-1", 1, evidence_json=_checklist("pr-1", "ok", "attention", "attention", "unknown")),
                _row("pr-2", 2, evidence_json=_checklist("pr-2", "ok", "unknown")),
                # The phrase sits in a detail, not in a status: not an attention change.
                _row("pr-3", 3, evidence_json=_checklist("pr-3", "ok", details=[{"status": "attention"}])),
                _row("pr-4", 4),
            ],
        )
    url = f"/api/repos/{repo['id']}/changes"

    plain = (await client.get(url)).json()
    assert all("evidence_summary" not in c for c in plain["items"])
    assert "checklists_built" not in plain

    with_evidence = (await client.get(url, params={"evidence": "true"})).json()
    assert with_evidence["checklists_built"] == {"built": 3, "changes": 4}
    rows = {c["id"]: c for c in with_evidence["items"]}
    assert rows["pr-1"]["evidence_summary"] == {
        "generated_at": "2026-10-14T00:00:00+00:00",
        "ok": 1,
        "attention": 2,
        "unknown": 1,
    }
    assert rows["pr-2"]["evidence_summary"]["attention"] == 0
    assert "evidence_summary" not in rows["pr-4"]
    assert seen == []

    flagged = (await client.get(url, params={"needs_attention": "true"})).json()
    assert flagged["total"] == 1 and [c["id"] for c in flagged["items"]] == ["pr-1"]
    paged = (await client.get(url, params={"needs_attention": "true", "offset": 1})).json()
    assert paged["total"] == 1 and paged["items"] == []


@pytest.mark.asyncio
async def test_needs_attention_finds_what_save_evidence_wrote(client: AsyncClient, app) -> None:
    from repowise.core.changes.collect import save_evidence

    repo = await create_test_repo(client)
    await _seed(app, repo["id"])  # pr-1 feature, pr-2 fix, pr-3 feature
    async with get_session(app.state.session_factory) as session:
        await save_evidence(session, repo["id"], "pr-1", _evidence("pr-1", "attention"))
        await save_evidence(session, repo["id"], "pr-2", _evidence("pr-2", "ok", "attention"))
        await save_evidence(session, repo["id"], "pr-3", _evidence("pr-3", "ok"))
    url = f"/api/repos/{repo['id']}/changes"

    flagged = (await client.get(url, params={"needs_attention": "true", "evidence": "true"})).json()
    assert [c["id"] for c in flagged["items"]] == ["pr-2", "pr-1"] and flagged["total"] == 2
    assert flagged["items"][0]["evidence_summary"]["attention"] == 1
    fixes = (
        await client.get(url, params={"needs_attention": "true", "category": "fix", "evidence": "true"})
    ).json()
    assert fixes["total"] == 1 and [c["id"] for c in fixes["items"]] == ["pr-2"]
    # The built count follows the category, not the attention filter.
    assert fixes["checklists_built"] == {"built": 1, "changes": 1}
    # Paging runs after the confirm, over the confirmed set.
    second = (await client.get(url, params={"needs_attention": "true", "limit": 1, "offset": 1})).json()
    assert second["total"] == 2 and [c["id"] for c in second["items"]] == ["pr-1"]


def test_evidence_summary_never_reads_missing_or_unknown_status_as_clean() -> None:
    from types import SimpleNamespace

    from repowise.core.changes.collect import evidence_summary

    odd = SimpleNamespace(evidence_json=_checklist("pr-1", "ok", "skipped"))
    assert evidence_summary(odd) == {
        "generated_at": "2026-10-14T00:00:00+00:00",
        "ok": 1,
        "attention": 0,
        "unknown": 1,
    }
    empty = evidence_summary(SimpleNamespace(evidence_json=_checklist("pr-1")))
    assert empty is not None and (empty["ok"], empty["attention"], empty["unknown"]) == (0, 0, 0)


def _fake_outcomes(monkeypatch, route, refreshed: list[str], gate=None) -> None:
    from repowise.core.changes.evidence import EvidenceItem

    async def _stale(_root, evidence, key=None):
        return evidence.item("outcomes") is None

    async def _key(_root):
        return ("h", 30)

    async def _item(session, repo_id, root, row, key):
        refreshed.append(row.change_id)
        if gate is not None:
            await gate.wait()
        value = {"as_of": key[0], "days": key[1]}
        return EvidenceItem("outcomes", "Outcomes since merge", "ok", "declared", value)

    monkeypatch.setattr(route, "outcomes_stale", _stale)
    monkeypatch.setattr(route, "outcomes_key", _key)
    monkeypatch.setattr(route, "outcomes_item", _item)


@pytest.mark.asyncio
async def test_kept_evidence_has_only_its_outcomes_read_again_when_head_moved(
    client: AsyncClient, app, monkeypatch
) -> None:
    seen: list[str] = []
    refreshed: list[str] = []
    route = _fake_build(monkeypatch, seen)
    _fake_outcomes(monkeypatch, route, refreshed)
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}/changes/pr-1"

    await client.get(base, params={"evidence": "true"})
    record = (await client.get(base, params={"evidence": "true"})).json()
    kinds = [i["kind"] for i in record["evidence"]["items"]]
    assert kinds == ["health_delta", "outcomes"] and seen == ["pr-1"] and refreshed == ["pr-1"]
    # Merged into what was stored: the next read rebuilds nothing.
    again = (await client.get(base, params={"evidence": "true"})).json()
    assert again["evidence"] == record["evidence"]
    assert seen == ["pr-1"] and refreshed == ["pr-1"]


@pytest.mark.asyncio
async def test_a_refresh_is_not_swallowed_by_an_outcomes_read_in_flight(
    client: AsyncClient, app, monkeypatch
) -> None:
    import asyncio

    seen: list[str] = []
    refreshed: list[str] = []
    gate = asyncio.Event()
    route = _fake_build(monkeypatch, seen)
    _fake_outcomes(monkeypatch, route, refreshed, gate)
    monkeypatch.setattr(route, "EVIDENCE_WAIT_SECONDS", 0.05)
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}/changes/pr-1"

    gate.set()
    await client.get(base, params={"evidence": "true"})  # full build, kept without outcomes
    gate.clear()
    waiting = (await client.get(base, params={"evidence": "true"})).json()
    assert waiting["evidence_building"] is True and refreshed == ["pr-1"]
    # The outcomes read is still running; a refresh runs its own full build.
    await client.get(base, params={"evidence": "true", "refresh": "true"})
    assert seen == ["pr-1", "pr-1"]
    gate.set()
    for _ in range(100):
        if not route._BUILDS:
            break
        await asyncio.sleep(0.01)
    done = (await client.get(base, params={"evidence": "true"})).json()
    assert [i["kind"] for i in done["evidence"]["items"]] == ["health_delta", "outcomes"]


@pytest.mark.asyncio
async def test_suspects_rank_the_window_with_evidence(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    url = f"/api/repos/{repo['id']}/suspects"

    got = (
        await client.post(
            url, json={"symptom": "crash", "since": "2026-01-01T00:00:00Z", "until": "2026-01-10T00:00:00Z"}
        )
    ).json()
    assert got["candidates"] == 3
    assert [s["id"] for s in got["suspects"]][:1] == ["pr-2"]
    assert got["suspects"][0]["signals"]["text"]["evidence"] == ["term crash"]
    assert "probability" in got["note"]

    assert (await client.post(url, json={})).status_code == 422
    assert (await client.post(url, json={"symptom": "x", "until": "soon"})).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(("since", "count", "capped"), [
    ("2026-01-01T00:00:00Z", 3, True),
    ("2026-01-02T00:00:00Z", 2, False),
    ("2026-01-04T00:00:00Z", 0, False),
])
async def test_suspects_http_preserves_candidate_cap_status(
    client: AsyncClient, app, tmp_path: Path, since: str, count: int, capped: bool,
) -> None:
    from repowise.core.repo_config import save_repo_config

    repo = await create_test_repo(client, tmp_path)
    await _seed(app, repo["id"])
    save_repo_config(repo["local_path"], {"commit_limit": 3})
    response = await client.post(
        f"/api/repos/{repo['id']}/suspects",
        json={"symptom": "crash", "since": since, "until": "2026-01-10T00:00:00Z", "limit": 1},
    )
    assert response.status_code == 200, response.text
    got = response.json()
    assert got["candidates"] == count
    assert got["candidates_capped"] is capped
    assert len(got["suspects"]) == min(count, 1)

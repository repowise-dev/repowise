"""In a workspace, every dead-code reader withdraws what the cross-repo cut withdraws.

One finding is stored deletion-ready and reads safe on every surface outside a
workspace; another repo changes together with its file, so inside one it
reads not safe everywhere, at the confidence ``get_dead_code`` shows. A second
finding serves an HTTP contract, so its confidence drops wherever it is shown.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo

SAFE_PATH = "src/helpers.py"
CONTRACT_PATH = "src/served.py"


class _Consumed:
    """Another repo co-changes with SAFE_PATH and calls an HTTP route in CONTRACT_PATH."""

    def has_cross_repo_consumers(self, repo_alias, file_path):
        return [{"repo": "web"}] if file_path == SAFE_PATH else []

    def get_repos_depending_on(self, repo_alias):
        return []

    def get_package_consumers(self, repo_alias):
        return []

    def get_contract_links_as_provider(self, repo_alias, file_path):
        return [{"consumer_repo": "web"}] if file_path == CONTRACT_PATH else []


CUT = {"lookup": _Consumed(), "alias": "api"}


async def _seed(session_factory, repo_id: str) -> None:
    from repowise.core.persistence.models import GitMetadata

    async with get_session(session_factory) as session:
        # File detail answers for an indexed file only.
        session.add(GitMetadata(repository_id=repo_id, file_path=SAFE_PATH))
        await crud.save_dead_code_findings(
            session,
            repo_id,
            [
                {
                    "kind": "unused_export",
                    "file_path": SAFE_PATH,
                    "symbol_name": "old_helper",
                    "symbol_kind": "function",
                    "confidence": 0.9,
                    "reason": "No callers",
                    "lines": 40,
                    "start_line": 1,
                    "end_line": 40,
                    "safe_to_delete": True,
                },
                {
                    "kind": "unreachable_file",
                    "file_path": CONTRACT_PATH,
                    "symbol_name": None,
                    "symbol_kind": None,
                    "confidence": 0.9,
                    "reason": "No imports found",
                    "lines": 30,
                    "safe_to_delete": False,
                },
            ],
        )


def _enter_workspace(app, tmp_path, monkeypatch, repo_id: str) -> None:
    state = app.state
    monkeypatch.setattr(state, "cross_repo_enricher", CUT["lookup"], raising=False)
    config = SimpleNamespace(repos=[SimpleNamespace(alias="api", path=".")])
    monkeypatch.setattr(state, "workspace_config", config, raising=False)
    monkeypatch.setattr(state, "workspace_root", str(tmp_path), raising=False)
    path_to_id = {str(tmp_path.resolve()): repo_id}
    monkeypatch.setattr(state, "workspace_path_to_repo_id", path_to_id, raising=False)


def _dead_actions(view: dict) -> list[dict]:
    return [
        a for h in view["horizons"].values() for a in h["actions"] if a["rule"] == "dead_code_batch"
    ]


async def _rest_reads(client: AsyncClient, rid: str) -> dict:
    """What each REST reader says about SAFE_PATH's finding."""
    detail = (await client.get(f"/api/repos/{rid}/files/{SAFE_PATH}")).json()
    [row] = detail["dead_code"]
    overview = (await client.get(f"/api/repos/{rid}/overview-summary")).json()
    attention = [i for i in overview["attention"] if i["type"] == "dead_code"]
    related = (
        await client.post(f"/api/repos/{rid}/health/related-work", json={"file_paths": [SAFE_PATH]})
    ).json()
    [item] = related["files"][0]["lenses"]["dead_code"]["items"]
    actions = (await client.get(f"/api/repos/{rid}/actions")).json()
    listed = (await client.get(f"/api/repos/{rid}/dead-code")).json()
    [served] = [f for f in listed if f["file_path"] == SAFE_PATH]
    return {
        "file detail": (row["confidence"], row["safe_to_delete"]),
        "dead-code list": (served["confidence"], served["safe_to_delete"]),
        "attention": [i["target_id"] for i in attention],
        "related work": item["tier"],
        "actions": [a["evidence_total"] for a in _dead_actions(actions)],
    }


@pytest.mark.asyncio
async def test_every_rest_reader_withdraws_a_stored_safe_row_the_cut_reaches(
    client: AsyncClient, app, tmp_path, monkeypatch
) -> None:
    from repowise.server.routers import actions

    app.include_router(actions.router)
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    # Outside a workspace the row is deletion-ready everywhere, so the cut is
    # what the second read measures.
    assert await _rest_reads(client, repo["id"]) == {
        "file detail": (0.9, True),
        "dead-code list": (0.9, True),
        "attention": [SAFE_PATH],
        "related work": "safe_to_delete",
        "actions": [1],
    }
    _enter_workspace(app, tmp_path, monkeypatch, repo["id"])
    assert await _rest_reads(client, repo["id"]) == {
        "file detail": (0.9, False),
        "dead-code list": (0.9, False),
        "attention": [],
        "related work": "review",
        "actions": [],
    }


@pytest.mark.asyncio
async def test_core_readers_take_the_cut(session_factory) -> None:
    from repowise.core.persistence.crud.analysis.actions import (
        load_action,
        load_actions_view,
        write_actions_snapshot,
    )
    from repowise.core.report.model import ReportContext
    from repowise.core.report.sections.dead_code import collect
    from repowise.server.services.node_signals import collect_node_signals
    from tests.unit.persistence.helpers import insert_repo

    async with get_session(session_factory) as session:
        rid = (await insert_repo(session)).id
    await _seed(session_factory, rid)

    async with get_session(session_factory) as session:
        # The snapshot is written per repository, without the cut.
        await write_actions_snapshot(session, rid)
        [stored] = _dead_actions(await load_actions_view(session, rid))
        assert _dead_actions(await load_actions_view(session, rid, **CUT)) == []
        assert await load_action(session, rid, stored["id"], **CUT) is None

        report = ReportContext(session, rid, ".", alias="api", cross_repo=CUT)
        tiers = {c["name"]: c["data"] for c in await collect(report)}
        # The contract cut takes CONTRACT_PATH's 0.9 to 0.27, out of high.
        assert tiers["dead-code:high"]["count"] == 1
        assert tiers["dead-code:low"]["count"] == 1

        signals = await collect_node_signals(session, rid, [CONTRACT_PATH], **CUT)
        assert signals[CONTRACT_PATH].dead_confidence == 0.27
        # Dead-file counts (module, architecture, community) read is_dead only.
        uncut = await collect_node_signals(session, rid, [CONTRACT_PATH])
        assert uncut[CONTRACT_PATH].is_dead is signals[CONTRACT_PATH].is_dead is True


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", [{}, CUT], ids=["single repo", "workspace"])
async def test_a_snapshot_that_took_the_stored_flag_is_not_served(
    session_factory, monkeypatch, cut
) -> None:
    """A row stored safe below the confidence bar, counted by a snapshot built
    from the stored flag, never reaches Do next."""
    from repowise.core.analysis.actions import build
    from repowise.core.persistence.crud.analysis.actions import (
        _KEY,
        SNAPSHOT_KIND,
        load_actions_view,
        write_actions_snapshot,
    )
    from repowise.core.persistence.read_snapshots import read_snapshot
    from tests.unit.persistence.helpers import insert_repo

    async with get_session(session_factory) as session:
        rid = (await insert_repo(session)).id
        await crud.save_dead_code_findings(
            session,
            rid,
            [
                {
                    "kind": "unused_export",
                    "file_path": "src/legacy.py",
                    "symbol_name": "legacy",
                    "symbol_kind": "function",
                    "confidence": 0.5,
                    "reason": "No callers",
                    "lines": 40,
                    "start_line": 1,
                    "end_line": 40,
                    "safe_to_delete": True,
                }
            ],
        )
    with monkeypatch.context() as m:
        m.setattr(build, "is_safe", lambda r: bool(build.field(r, "safe_to_delete")))
        async with get_session(session_factory) as session:
            await write_actions_snapshot(session, rid)
            stored = await read_snapshot(session, rid, SNAPSHOT_KIND, _KEY)
            assert "dead_code_batch" in str(stored)

    async with get_session(session_factory) as session:
        assert _dead_actions(await load_actions_view(session, rid, **cut)) == []


@pytest.mark.asyncio
async def test_chat_dataset_and_mcp_actions_take_the_cut(session_factory, monkeypatch) -> None:
    from repowise.server.chat_datasets import LoadContext
    from repowise.server.chat_datasets.findings import load_dead_code
    from repowise.server.mcp_server import _helpers, _state
    from repowise.server.mcp_server.tool_overview.actions import _build_next_actions
    from tests.unit.persistence.helpers import insert_repo

    async with get_session(session_factory) as session:
        repository = await insert_repo(session)
    await _seed(session_factory, repository.id)
    monkeypatch.setattr(_helpers, "_is_workspace_mode", lambda: True)
    monkeypatch.setattr(_state, "_cross_repo_enricher", CUT["lookup"])

    async def no_files(_name):
        return []

    async with get_session(session_factory) as session:
        context = LoadContext(session, repository, ".", no_files, "api")
        rows = {r["path"]: r for r in await load_dead_code(context)}
        assert (rows[SAFE_PATH]["confidence"], rows[SAFE_PATH]["safe_to_delete"]) == (0.9, False)
        assert rows[CONTRACT_PATH]["confidence"] == 0.27

        block, _ = await _build_next_actions(session, repository, full=True, alias="api")
        assert "dead_code_batch" not in str(block)

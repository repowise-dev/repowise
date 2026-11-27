"""/evidence/measured and /evidence/status when a workload's newest run ranks nothing.

The per-workload view lists the newest run that ranks something
(``evidence.hotness.derive_hotness``); the measured payload names the newer
run beside it and why it ranks nothing, and the status says when the newest
evidence is ahead of the index.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from repowise.core.persistence.crud import replace_symbol_hotness, replace_workload_hotness
from repowise.core.persistence.models import EvidenceRun
from repowise.server.routers import runtime_evidence
from tests.unit.server.test_runtime_evidence_routes import HEAD, _ranked, _repo

NEWER = "d" * 40
AT = datetime(2026, 11, 15, tzinfo=UTC)
FN = "src/app.py::run"


@pytest.fixture
async def ev_client(app, client: AsyncClient) -> AsyncClient:
    app.include_router(runtime_evidence.router)
    return client


def _run(run_id: str, commit: str, at: datetime, *, kind: str = "profile") -> EvidenceRun:
    return EvidenceRun(
        id=run_id,
        repository_id="",
        run_key=run_id,
        kind=kind,
        format="repowise-evidence-v1",
        source="cli",
        workload_name="init",
        workload_class="entry_point",
        value_kind="wall",
        unit="seconds",
        commit_sha=commit,
        measured_at=at,
        ingested_at=at,
        total_value=1000.0,
        repo_self_value=130.0,
        wait_value=580.0,
        library_value=10.0,
    )


async def _seed(session_factory, repo_id: str, runs: list[EvidenceRun], ranked: bool) -> None:
    async with session_factory() as session:
        for run in runs:
            run.repository_id = repo_id
        session.add_all(runs)
        await session.flush()
        # What the derivation writes: the older run at the index ranks, the newer
        # one leaves only stale rows.
        rows = [{"symbol_id": FN, "tier": "measured", "run_id": runs[-1].id, "freshness": "stale"}]
        await replace_symbol_hotness(session, repo_id, rows)
        await replace_workload_hotness(session, repo_id, [_ranked(FN, "old", 1)] if ranked else [])
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("newer_commit", "anchor", "reason"),
    [
        # Measured after the indexed commit's run: ranks once the index gets there.
        (NEWER, True, "ahead_of_index"),
        # At the indexed commit and still nothing ranked: thin evidence.
        (HEAD, True, "not_ranked"),
        # Another commit and nothing at the index to place it against.
        (NEWER, False, "stale"),
    ],
)
async def test_the_listed_run_names_the_newer_run_and_why_it_ranks_nothing(
    ev_client: AsyncClient, session_factory, newer_commit: str, anchor: bool, reason: str
) -> None:
    repo_id = await _repo(ev_client, session_factory)
    old = _run("old", HEAD if anchor else "e" * 40, AT)
    new = _run("new", newer_commit, AT + timedelta(days=1))
    await _seed(session_factory, repo_id, [old, new], ranked=True)

    measured = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured")).json()
    assert measured["status"] == "measured"
    (run,) = measured["measured"]["runs"]
    assert run["run_id"] == "old"
    assert run["newer_run"] == {
        "run_id": "new",
        "commit": newer_commit,
        "measured_at": (AT + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "reason": reason,
    }
    assert [r["symbol_id"] for r in measured["measured"]["rows"]] == [FN]
    # The status view still names the newest run of the workload.
    status = (await ev_client.get(f"/api/repos/{repo_id}/evidence/status")).json()
    assert [w["run_id"] for w in status["workloads"]] == ["new"]


@pytest.mark.asyncio
async def test_the_newest_listed_run_has_no_newer_run(
    ev_client: AsyncClient, session_factory
) -> None:
    repo_id = await _repo(ev_client, session_factory)
    # An older run, and a partial newer one that is never the workload's newest.
    partial = _run("partial", HEAD, AT + timedelta(days=2))
    partial.mapping_partial = True
    await _seed(
        session_factory,
        repo_id,
        [_run("before", HEAD, AT - timedelta(days=1)), _run("old", HEAD, AT), partial],
        ranked=True,
    )
    measured = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured")).json()
    (run,) = measured["measured"]["runs"]
    assert (run["run_id"], run["newer_run"]) == ("old", None)
    # The rest of the time: 1000 less 130 + 580 + 10.
    assert run["unattributed_share"] == 0.28


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("anchor", "status", "command"),
    [
        # The workload's run at the indexed commit (no production frame) places it.
        ("profile", "ahead_of_index", "repowise update"),
        # Another kind at the indexed commit says nothing about the profile.
        ("test_timing", "stale", "repowise evidence setup --format json"),
        (None, "stale", "repowise evidence setup --format json"),
    ],
)
async def test_nothing_ranked_and_the_newest_run_ahead_of_the_index_says_so(
    ev_client: AsyncClient, session_factory, anchor: str | None, status: str, command: str
) -> None:
    repo_id = await _repo(ev_client, session_factory)
    runs = [_run("at-index", HEAD, AT, kind=anchor)] if anchor else []
    runs.append(_run("new", NEWER, AT + timedelta(days=1)))
    await _seed(session_factory, repo_id, runs, ranked=False)

    measured = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured")).json()
    assert (measured["basis"], measured["status"], measured["command"]) == (
        "unknown",
        status,
        command,
    )
    got = (await ev_client.get(f"/api/repos/{repo_id}/evidence/status")).json()
    assert (got["status"], got["command"]) == (status, command)

"""The records an agent's discovery pass checks, and the agreements it can move.

The pass is about background on the busiest production code: what an agent
editing that code would most often miss. Each fixture below puts the wanted
order against insertion and id order, so an unranked read cannot pass.
"""

from __future__ import annotations

import json

from repowise.core.persistence.crud.authority import accept_decision, dismiss_candidate
from repowise.core.persistence.crud.decision_discovery import (
    agreement_records,
    decision_prompt_for,
    discovery_records,
)
from repowise.core.persistence.models import DecisionRecord, GitMetadata, HealthFileMetric
from tests.unit.persistence.helpers import insert_repo


async def _record(session, repo_id, rec_id, files, **overrides) -> DecisionRecord:
    fields = {
        "title": f"title {rec_id}",
        "decision": f"decision {rec_id}",
        "rationale": "why",
        "status": "proposed",
        "source": "pr",
        **overrides,
    }
    rec = DecisionRecord(
        id=rec_id, repository_id=repo_id, affected_files_json=json.dumps(files), **fields
    )
    session.add(rec)
    await session.flush()
    return rec


async def _accept(session, rec: DecisionRecord) -> None:
    await accept_decision(
        session, rec, accepter="t", evidence=[f"seed:{rec.id}"], scope=["src/app.py"]
    )
    await session.commit()


def _git(repo_id, path, *, hot=False, score=None, churn=0.0) -> GitMetadata:
    return GitMetadata(
        repository_id=repo_id,
        file_path=path,
        is_hotspot=hot,
        temporal_hotspot_score=score,
        churn_percentile=churn,
    )


async def test_busiest_production_file_first(async_session):
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            _git(repo.id, "src/cold.py", churn=0.1),
            _git(repo.id, "src/warm.py", hot=True, score=0.4, churn=0.9),
            _git(repo.id, "src/hot.py", hot=True, score=0.9, churn=0.5),
        ]
    )
    await _record(async_session, repo.id, "a1", ["src/cold.py"])
    await _record(async_session, repo.id, "b2", ["src/warm.py"])
    await _record(async_session, repo.id, "c3", ["src/cold.py", "src/hot.py"])
    await async_session.commit()

    found = await discovery_records(async_session, repo.id)

    assert [r["id"] for r in found] == ["c3", "b2", "a1"]
    # The busiest file leads the record's own list.
    assert found[0]["files"] == ["src/hot.py", "src/cold.py"]


async def test_only_production_code_with_history_counts(async_session):
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            _git(repo.id, "src/gen_pb2.py", hot=True, score=0.99),
            _git(repo.id, "tests/test_app.py", hot=True, score=0.98),
            _git(repo.id, "src/app.py", score=0.1),
            HealthFileMetric(repository_id=repo.id, file_path="src/gen_pb2.py", code_origin="generated"),
        ]
    )
    await _record(async_session, repo.id, "gen", ["src/gen_pb2.py"])
    await _record(async_session, repo.id, "tst", ["tests/test_app.py"])
    await _record(async_session, repo.id, "none", ["src/never_in_git.py"])
    await _record(async_session, repo.id, "app", ["src/app.py"])
    await async_session.commit()

    assert [r["id"] for r in await discovery_records(async_session, repo.id)] == ["app"]


async def test_leaves_out_what_is_not_background(async_session):
    repo = await insert_repo(async_session)
    async_session.add(_git(repo.id, "src/app.py", hot=True, score=0.5))
    await _record(async_session, repo.id, "bg", ["src/app.py"])
    await _record(async_session, repo.id, "agent", ["src/app.py"], source="agent")
    await _record(async_session, repo.id, "agree", ["src/app.py"], kind="agreement")
    await _record(async_session, repo.id, "footprint", ["src/app.py"], scope_basis="commit_footprint")
    gone = await _record(async_session, repo.id, "gone", ["src/app.py"])
    inforce = await _record(async_session, repo.id, "inforce", ["src/app.py"])
    await async_session.commit()
    await _accept(async_session, inforce)
    await dismiss_candidate(async_session, gone, reason="a bug fix", accepter="t", kind="agent")
    await async_session.commit()

    assert [r["id"] for r in await discovery_records(async_session, repo.id)] == ["bg"]


async def test_capped(async_session):
    repo = await insert_repo(async_session)
    async_session.add(_git(repo.id, "src/app.py", hot=True, score=0.5))
    for i in range(25):
        await _record(async_session, repo.id, f"r{i:02d}", ["src/app.py"])
    await async_session.commit()

    prompt = await decision_prompt_for(async_session, repo.id, "discover")

    assert prompt.kind == "discover"
    assert len(prompt.ids) == 20


async def test_no_background_falls_back_to_recording(async_session):
    repo = await insert_repo(async_session)
    await async_session.commit()

    prompt = await decision_prompt_for(async_session, repo.id, "discover")

    assert prompt.kind == "add" and prompt.ids == ()
    assert "repowise decision add" in prompt.text


async def test_agreements_in_force_first_and_retired_left_out(async_session):
    repo = await insert_repo(async_session)
    await _record(async_session, repo.id, "bg", [], kind="agreement")
    law = await _record(async_session, repo.id, "law", [], kind="agreement")
    await _record(async_session, repo.id, "old", [], kind="agreement", status="dismissed")
    await _record(async_session, repo.id, "code", ["src/app.py"])
    await async_session.commit()
    await _accept(async_session, law)

    found = await agreement_records(async_session, repo.id)

    assert [r["id"] for r in found] == ["law", "bg"]
    prompt = await decision_prompt_for(async_session, repo.id, "agreements")
    assert prompt.ids == ("law", "bg")
    assert "CLAUDE.md" in prompt.text

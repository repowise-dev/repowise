"""bulk_upsert_decisions: a commit-mined decision folds into the PR decision of its merge."""

from __future__ import annotations

import json

from sqlalchemy import select

from repowise.core.persistence.crud import bulk_upsert_decisions, list_decision_evidence
from repowise.core.persistence.crud.decision_ingest import _headline_identity
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import insert_repo

_MERGE = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
_OTHER = "0f9e8d7c6b5a49382716051f2e3d4c5b6a798001"


def _pr(commits=(_MERGE,)):
    return {
        "title": "Cache parsed manifests between runs",
        "decision": "Parsed manifests are cached on disk and reused",
        "rationale": "Parsing dominated warm start time",
        "source": "pr",
        "status": "proposed",
        "evidence_commits": list(commits),
        "affected_files": ["src/manifest/cache.py"],
        "confidence": 0.8,
        "verification": "fuzzy",
        "source_quote": "cache parsed manifests on disk",
    }


def _arch(sha=_MERGE[:8]):
    # The miner echoes the short sha its prompt showed.
    return {
        "title": "Introduce an on-disk manifest cache",
        "decision": "Manifests are parsed once and cached",
        "rationale": "inferred from commit",
        "source": "git_archaeology",
        "status": "proposed",
        "evidence_commits": [sha],
        "affected_files": ["src/manifest/loader.py"],
        "confidence": 0.7,
        "verification": "fuzzy",
        "source_quote": "add manifest cache",
    }


async def _rows(session, repo_id):
    result = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
    )
    return list(result.scalars().all())


async def test_archaeology_on_the_pr_merge_commit_folds_into_the_pr_record(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_arch(), _pr()])

    (rec,) = await _rows(async_session, repo.id)
    assert rec.source == "pr"
    assert rec.title == "Cache parsed manifests between runs"
    assert json.loads(rec.affected_files_json) == [
        "src/manifest/cache.py",
        "src/manifest/loader.py",
    ]
    evidence = await list_decision_evidence(async_session, rec.id)
    assert {e.source for e in evidence} == {"pr", "git_archaeology"}
    # The surviving id is the one the PR decision derives on its own.
    assert rec.id == _headline_identity(repo.id, _pr(), [_pr()]).id


async def test_archaeology_folds_into_a_stored_pr_record(async_session):
    repo = await insert_repo(async_session)
    (pr_id,) = await bulk_upsert_decisions(async_session, repo.id, [_pr()])

    touched = await bulk_upsert_decisions(async_session, repo.id, [_arch()])

    assert touched == [pr_id]
    (rec,) = await _rows(async_session, repo.id)
    assert rec.title == "Cache parsed manifests between runs"


async def test_archaeology_on_an_unrelated_commit_stays_separate(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_arch(sha=_OTHER), _pr()])

    rows = await _rows(async_session, repo.id)
    assert sorted(r.source for r in rows) == ["git_archaeology", "pr"]


async def test_pr_without_a_merge_sha_does_not_fold(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_arch(), _pr(commits=())])

    rows = await _rows(async_session, repo.id)
    assert sorted(r.source for r in rows) == ["git_archaeology", "pr"]


def _second_pr(title="Drop the legacy manifest format"):
    return {**_pr(), "title": title, "source_quote": "drop the v1 manifest reader"}


async def test_several_pr_decisions_pair_by_title_overlap(async_session):
    repo = await insert_repo(async_session)
    arch = {**_arch(), "title": "Drop the legacy manifest format reader"}

    await bulk_upsert_decisions(async_session, repo.id, [arch, _pr(), _second_pr()])

    rows = await _rows(async_session, repo.id)
    assert sorted(r.title for r in rows) == [
        "Cache parsed manifests between runs",
        "Drop the legacy manifest format",
    ]


async def test_several_pr_decisions_without_a_clear_match_do_not_fold(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_arch(), _pr(), _second_pr()])

    assert len(await _rows(async_session, repo.id)) == 3


async def test_two_archaeology_decisions_on_one_pr_do_not_fold(async_session):
    repo = await insert_repo(async_session)
    other = {**_arch(), "title": "Parse manifests lazily", "source_quote": "lazy parse"}

    await bulk_upsert_decisions(async_session, repo.id, [_arch(), other, _pr()])

    assert len(await _rows(async_session, repo.id)) == 3


async def test_same_pr_title_on_two_merges_still_folds_each(async_session):
    repo = await insert_repo(async_session)
    pr_b = {**_pr(commits=(_OTHER,)), "source_quote": "cache manifests again"}

    await bulk_upsert_decisions(
        async_session, repo.id, [_arch(), _pr(), _arch(sha=_OTHER[:8]), pr_b]
    )

    rows = await _rows(async_session, repo.id)
    assert {r.source for r in rows} == {"pr"}


async def test_archaeology_restating_a_dismissed_pr_record_is_dropped(async_session):
    repo = await insert_repo(async_session)
    (pr_id,) = await bulk_upsert_decisions(async_session, repo.id, [_pr()])
    rec = await async_session.get(DecisionRecord, pr_id)
    rec.status = "dismissed"
    await async_session.flush()

    assert await bulk_upsert_decisions(async_session, repo.id, [_arch()]) == []
    assert len(await _rows(async_session, repo.id)) == 1

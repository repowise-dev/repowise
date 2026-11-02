"""One PR body re-mined on every update must not leave a record per wording.

Each pass rewords the title and picks its own span, so the files and quote the
id derives from move, and title match never fires. Fixtures are real clusters
from repowise-dev/repowise (2026-11-02), where 234 reviewable candidates were
79 decisions with up to 11 copies each.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import resolve_decision_id
from repowise.core.persistence.crud.decision_lane_fold import same_decision_text
from repowise.core.persistence.crud.decisions import derive_decision_id
from repowise.core.persistence.decision_id_migration import FOLD_REASON, apply_id_migration
from repowise.core.persistence.models import (
    DecisionAlias,
    DecisionCandidateMeta,
    DecisionEvidence,
    DecisionRecord,
)
from tests.unit.persistence.helpers import accept, insert_repo

# ---------------------------------------------------------------------------
# The text rule, on pairs mined from one commit
# ---------------------------------------------------------------------------

#: Same commit, same decision: a shorter or longer span, or a reflowed one.
REWORDED = [
    (
        "CostTracker gains an operation context (record_as)",
        "CostTracker gains an operation context (record_as) so decision extraction no "
        "longer masquerades as doc_generation; the six providers read the label instead "
        "of hardcoding it",
    ),
    (
        "They now take a flat floor of 600 estimated tokens",
        "They now take a flat floor of 600 estimated tokens, under the p25 of 620 "
        "measured across this repository's 4,115 source files",
    ),
    (
        "the route fetches slim unless the requested tab is one of those",
        "so the route fetches slim unless the requested tab is one of those, and tells "
        "the shell which tabs need a round trip on first click.",
    ),
    (
        "It carries one figure now. Maintainability moves to the ribbon beside the other "
        "cuts of the same scoring, and Open findings leaves it, since the tab row already "
        "counts them.",
        "Maintainability moves to the ribbon beside the other cuts of the same scoring, "
        "and Open findings leaves it, since the tab row already counts them.",
    ),
    (
        "Parsers keep per-line branch counts (lcov BRDA, Cobertura condition-coverage, "
        "JaCoCo mb/cb, both Clover shapes); stored per file (migration 0084).",
        "Parsers keep per-line branch counts (lcov BRDA, Cobertura\n  condition-coverage, "
        "JaCoCo mb/cb, both Clover shapes); stored per file\n  (migration 0084).",
    ),
]

#: Same commit, different decisions. Folding any of these loses one.
DISTINCT = [
    (  # 338034b4: three decisions in one PR body
        "`kind` joins `sort` and `authorship` as a server-side filter, so the band and "
        "fix filters narrow the whole repository.",
        "The feed defaults to recency.",
    ),
    (
        "The high band compares against the repo's own moderate/high boundary on the "
        "score axis, resolved once per request before the page is cut",
        "`kind` joins `sort` and `authorship` as a server-side filter, so the band and "
        "fix filters narrow the whole repository.",
    ),
    (  # 2357a937: two tuning decisions in one PR body
        "They now take a flat floor of 600 estimated tokens",
        "It now scales on `features.nf`, the changed-file count the scorer already put "
        "on the payload",
    ),
    (  # ce707823: one quoted paragraph, two decisions
        "Local models (ollama/, lmstudio/, llamacpp/, local/, mock) price at $0 for both "
        "indexing spend and savings, via a shared is_local_model check.",
        "Ollama records under an ollama/<model> label so a local index shows real call "
        "and token counts at zero cost instead of a blank.",
    ),
    (  # opposite rulings share every content word but the negation
        "The worker pool reads the cache directly and the queue is not used",
        "The worker pool reads the cache directly and the queue is used",
    ),
    (  # 48803a7c: a multi-claim span and one claim it bundles
        "changes table (migration 0110) filled at every index and extended on update; "
        "git_commits stores each commit's category, and update re-labels rows the "
        "classifier now reads differently. GIT_HISTORY_VERSION 3 rebuilds history once.",
        "git_commits stores each commit's category, and update re-labels rows the "
        "classifier now reads differently.",
    ),
]


@pytest.mark.parametrize(("a", "b"), REWORDED)
def test_a_reworded_span_is_the_same_decision(a, b):
    assert same_decision_text(a, b)
    assert same_decision_text(b, a)


@pytest.mark.parametrize(("a", "b"), DISTINCT)
def test_two_decisions_from_one_commit_stay_apart(a, b):
    assert not same_decision_text(a, b)
    assert not same_decision_text(b, a)


def test_a_short_opening_is_not_enough():
    assert not same_decision_text("We now use it", "We now use it for the parse cache")


# ---------------------------------------------------------------------------
# Ingest: a re-mined PR folds into the record already stored
# ---------------------------------------------------------------------------

_COMMIT = "ce707823a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
_COST = REWORDED[0][1]


def _mined(title: str, decision: str, *, commit: str = _COMMIT, files=None, **extra) -> dict:
    """One PR-lane extraction, the shape the miner hands to ingest."""
    d = {
        "title": title,
        "decision": decision,
        "rationale": "so the by-operation and hotspots views stop collapsing to one bucket",
        "source": "pr",
        "status": "proposed",
        "affected_files": files or ["packages/core/src/repowise/core/generation/cost_tracker.py"],
        "evidence_file": None,
        "evidence_commits": [commit],
        "confidence": 0.7,
        "verification": "exact",
        "source_quote": decision,
    }
    d.update(extra)
    return d


async def _count(session) -> int:
    return (await session.execute(select(func.count(DecisionRecord.id)))).scalar_one()


@pytest.mark.asyncio
async def test_a_re_mined_pr_keeps_the_accepted_id(async_session):
    repo = await insert_repo(async_session)
    [first] = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_mined("CostTracker operation context labels spend by operation", _COST)],
    )
    await accept(async_session, first)

    # The next update: new title, shorter span, another file, abbreviated sha.
    touched = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _mined(
                "CostTracker takes an operation label via record_as",
                REWORDED[0][0],
                commit=_COMMIT[:8],
                files=["packages/core/src/repowise/core/analysis/decisions/extractor.py"],
            )
        ],
    )

    assert touched == [first]
    assert await _count(async_session) == 1
    rec = await async_session.get(DecisionRecord, first)
    # A rewording of the same lane does not retitle an accepted record.
    assert rec.title == "CostTracker operation context labels spend by operation"
    assert rec.decision == _COST


@pytest.mark.asyncio
async def test_rewordings_in_one_batch_collapse(async_session):
    repo = await insert_repo(async_session)
    titles = [
        "CostTracker records an operation label per call",
        "CostTracker gains operation context",
        "Operation label passed via CostTracker context",
    ]
    touched = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _mined(titles[0], _COST),
            _mined(titles[1], REWORDED[0][0]),
            _mined(titles[2], _COST + " instead of hardcoding it,"),
        ],
    )
    assert len(set(touched)) == 1
    assert await _count(async_session) == 1


@pytest.mark.asyncio
async def test_a_second_decision_from_the_same_pr_is_its_own_record(async_session):
    repo = await insert_repo(async_session)
    local, ollama = DISTINCT[3]
    await bulk_upsert_decisions(
        async_session, repo.id, [_mined("Local models price at $0 via prefix check", local)]
    )
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_mined("Ollama records calls under an ollama/<model> label", ollama)],
    )
    assert await _count(async_session) == 2


@pytest.mark.asyncio
async def test_the_same_sentence_from_another_commit_is_its_own_record(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_mined("Label spend", _COST)])
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        # Another file too: with the same files and quote the evidence id
        # already makes them one record, commit or not.
        [_mined("Label spend again", _COST, commit="0123456789abcdef", files=["other.py"])],
    )
    assert await _count(async_session) == 2


@pytest.mark.asyncio
async def test_a_bundle_is_never_folded_into(async_session):
    repo = await insert_repo(async_session)
    claim = REWORDED[0][0]
    await bulk_upsert_decisions(
        async_session, repo.id, [_mined("Bundle", f"{claim} and more", needs_split=True)]
    )
    await bulk_upsert_decisions(async_session, repo.id, [_mined("Claim", claim)])
    assert await _count(async_session) == 2


@pytest.mark.asyncio
async def test_a_dismissed_record_swallows_its_rewordings(async_session):
    """A dismissal is a tombstone: rewording the sentence must not re-propose it."""
    repo = await insert_repo(async_session)
    [first] = await bulk_upsert_decisions(async_session, repo.id, [_mined("Label spend", _COST)])
    (await async_session.get(DecisionRecord, first)).status = "dismissed"
    await async_session.flush()

    touched = await bulk_upsert_decisions(
        async_session, repo.id, [_mined("Spend labelled by operation", REWORDED[0][0])]
    )
    assert touched == []
    assert await _count(async_session) == 1


# ---------------------------------------------------------------------------
# Repair: copies stored before the ingest check fold, and leave an alias
# ---------------------------------------------------------------------------


async def _stored(session, repo_id: str, title: str, decision: str, *, age: int, **kw):
    """A record as an earlier run left it: settled on its derived id."""
    files = kw.get("files", ["src/cost_tracker.py"])
    rid = derive_decision_id(
        repo_id,
        title,
        source="pr",
        evidence_file=None,
        affected_files=files,
        identity_quote=decision,
    )
    rec = DecisionRecord(
        id=rid,
        repository_id=repo_id,
        title=title,
        decision=decision,
        rationale="so the views stop collapsing to one bucket",
        source="pr",
        status=kw.get("status", "proposed"),
        identity_quote=decision,
        affected_files_json=json.dumps(files),
        evidence_commits_json=json.dumps([kw.get("commit", _COMMIT)]),
        created_at=datetime(2026, 10, 30, tzinfo=UTC) + timedelta(hours=age),
    )
    session.add(rec)
    session.add(
        DecisionEvidence(
            decision_id=rid,
            source="pr",
            evidence_commit=f"{age}{_COMMIT}"[:40],
            source_quote=decision,
        )
    )
    await session.flush()
    return rec


@pytest.mark.asyncio
async def test_stored_rewordings_fold_into_the_accepted_one(async_session):
    repo = await insert_repo(async_session)
    oldest = await _stored(
        async_session,
        repo.id,
        "CostTracker gains operation context",
        REWORDED[0][0],
        age=0,
        files=["a.py"],
    )
    chosen = await _stored(
        async_session,
        repo.id,
        "CostTracker operation context labels spend",
        _COST,
        age=5,
        files=["b.py"],
    )
    newer = await _stored(
        async_session,
        repo.id,
        "CostTracker takes an operation label",
        _COST + " instead of hardcoding it,",
        age=9,
        files=["a.py", "b.py"],
    )
    other = await _stored(async_session, repo.id, "Ollama label", DISTINCT[3][1], age=1)
    await accept(async_session, chosen.id)
    oldest_id, newer_id = oldest.id, newer.id

    plan = await apply_id_migration(async_session, repo.id)

    assert {r.old_id: r.new_id for r in plan.folds()} == {
        oldest_id: chosen.id,
        newer_id: chosen.id,
    }
    ids = set((await async_session.execute(select(DecisionRecord.id))).scalars().all())
    assert ids == {chosen.id, other.id}
    # Evidence the copies carried came with them.
    assert (
        await async_session.execute(
            select(func.count(DecisionEvidence.id)).where(DecisionEvidence.decision_id == chosen.id)
        )
    ).scalar_one() == 3
    for gone in (oldest_id, newer_id):
        alias = await async_session.get(DecisionAlias, gone)
        assert alias is not None and alias.reason == FOLD_REASON
        assert await resolve_decision_id(async_session, gone) == chosen.id

    # Idempotent.
    again = await apply_id_migration(async_session, repo.id)
    assert again.folds() == []


@pytest.mark.asyncio
async def test_without_an_acceptance_the_oldest_reading_keeps_the_id(async_session):
    repo = await insert_repo(async_session)
    old = await _stored(async_session, repo.id, "Flat floor", REWORDED[1][0], age=0)
    new = await _stored(
        async_session, repo.id, "600-token floor", REWORDED[1][1], age=3, files=["x.py"]
    )
    new_id = new.id
    plan = await apply_id_migration(async_session, repo.id)
    assert [(r.old_id, r.new_id) for r in plan.folds()] == [(new_id, old.id)]


@pytest.mark.asyncio
async def test_two_accepted_copies_are_both_kept(async_session):
    """Folding an accepted record away would retire what a person signed."""
    repo = await insert_repo(async_session)
    a = await _stored(async_session, repo.id, "Flat floor", REWORDED[1][0], age=0)
    b = await _stored(
        async_session, repo.id, "600-token floor", REWORDED[1][1], age=3, files=["x.py"]
    )
    await accept(async_session, a.id)
    await accept(async_session, b.id)
    plan = await apply_id_migration(async_session, repo.id)
    assert plan.folds() == []
    assert await _count(async_session) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("a", "b"), DISTINCT)
async def test_stored_distinct_decisions_from_one_commit_stay(async_session, a, b):
    repo = await insert_repo(async_session)
    await _stored(async_session, repo.id, "first", a, age=0)
    await _stored(async_session, repo.id, "second", b, age=1, files=["y.py"])
    plan = await apply_id_migration(async_session, repo.id)
    assert plan.folds() == []
    assert await _count(async_session) == 2


@pytest.mark.asyncio
async def test_a_flagged_bundle_is_held_out_of_the_repair(async_session):
    repo = await insert_repo(async_session)
    claim = await _stored(async_session, repo.id, "claim", REWORDED[1][0], age=0)
    bundle = await _stored(async_session, repo.id, "bundle", REWORDED[1][1], age=1, files=["y.py"])
    async_session.add(
        DecisionCandidateMeta(decision_id=bundle.id, repository_id=repo.id, needs_split=True)
    )
    await async_session.flush()
    claim_id = claim.id
    plan = await apply_id_migration(async_session, repo.id)
    assert plan.folds() == []
    assert await async_session.get(DecisionRecord, claim_id) is not None


@pytest.mark.asyncio
async def test_the_same_sentence_from_another_lane_is_left_to_the_lane_fold(async_session):
    """PR body against commit body is ``fold_archaeology_into_pr``'s pairing, by title."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_mined("Label spend", _COST)])
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _mined(
                "Spend labelled by operation",
                REWORDED[0][0],
                source="git_archaeology",
                files=["other.py"],
            )
        ],
    )
    # One PR title on the commit, so the lane fold pairs it: one record, PR id.
    assert await _count(async_session) == 1


@pytest.mark.asyncio
async def test_a_dismissed_copy_keeps_its_rewordings_out_of_review(async_session):
    repo = await insert_repo(async_session)
    tomb = await _stored(
        async_session, repo.id, "Flat floor", REWORDED[1][1], age=5, status="dismissed"
    )
    copy = await _stored(async_session, repo.id, "600 floor", REWORDED[1][0], age=0, files=["x.py"])
    copy_id = copy.id
    plan = await apply_id_migration(async_session, repo.id)
    assert [(r.old_id, r.new_id) for r in plan.folds()] == [(copy_id, tomb.id)]


@pytest.mark.asyncio
async def test_a_reviewed_candidate_is_never_folded_away(async_session):
    repo = await insert_repo(async_session)
    await _stored(async_session, repo.id, "Flat floor", REWORDED[1][0], age=0)
    later = await _stored(
        async_session, repo.id, "600 floor", REWORDED[1][1], age=3, files=["x.py"]
    )
    async_session.add(
        DecisionCandidateMeta(decision_id=later.id, repository_id=repo.id, review_state="merged")
    )
    await async_session.flush()
    plan = await apply_id_migration(async_session, repo.id)
    assert plan.folds() == []

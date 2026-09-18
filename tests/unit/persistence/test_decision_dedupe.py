"""The retroactive dedupe sweep: what it folds, and what it refuses to fold.

The rule under test is that a record folds only into a record it is itself
measured against. The angles below are chosen so A-B and B-C clear the
threshold while A-C does not, which is the case a connected-component sweep
gets wrong.
"""

from __future__ import annotations

import json
import math

import pytest
from sqlalchemy import select

from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.persistence.crud.authority import (
    accept_decision,
    resolve_decision_id,
)
from repowise.core.persistence.decision_dedupe import (
    apply_dedupe,
    plan_dedupe,
    render_plan,
)
from repowise.core.persistence.models import (
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
)
from repowise.core.persistence.vector_store import InMemoryVectorStore
from tests.unit.persistence.helpers import insert_repo

TAU = 0.8

#: Angle per marker, in degrees. 35 apart clears TAU (cos 35 = 0.819); 70
#: apart does not (cos 70 = 0.342), so "a" and "c" are neighbours of "b"
#: without being neighbours of each other.
_ANGLES = {"a": 0.0, "b": 35.0, "c": 70.0, "far": 89.0}


class _AngleEmbedder:
    """Places each text on a circle by the ``@<marker>`` token it carries.

    Lets a test state the cosine between two decisions directly instead of
    hoping a keyword heuristic lands on the intended side of the threshold.
    """

    dimensions = 2

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            marker = next((m for m in _ANGLES if f"@{m}" in text), "far")
            theta = math.radians(_ANGLES[marker])
            out.append([math.cos(theta), math.sin(theta)])
        return out


def _store() -> InMemoryVectorStore:
    return InMemoryVectorStore(_AngleEmbedder())


async def _seed(session, store, repo_id, specs: list[dict]) -> list[DecisionRecord]:
    """Write records and their vectors the way the index leaves them."""
    from repowise.core.analysis.decisions.semantic_match import upsert_decision_vector

    records = []
    for i, spec in enumerate(specs):
        rec = DecisionRecord(
            id=f"rec{i:04d}" + "0" * 22,
            repository_id=repo_id,
            title=spec["title"],
            status=spec.get("status", "proposed"),
            decision=spec.get("decision", ""),
            rationale=spec.get("rationale", ""),
            context="",
            source=spec.get("source", "pr"),
            kind=spec.get("kind", "architectural"),
            confidence=spec.get("confidence", 0.5),
            affected_files_json=json.dumps(spec.get("files", [])),
            affected_modules_json=json.dumps([]),
            evidence_commits_json=json.dumps(spec.get("commits", [])),
            tags_json=json.dumps([]),
            alternatives_json=json.dumps([]),
            consequences_json=json.dumps([]),
        )
        session.add(rec)
        records.append(rec)
    await session.flush()
    for rec in records:
        await upsert_decision_vector(
            store, rec.id, title=rec.title, decision=rec.decision or ""
        )
    return records


@pytest.fixture
async def repo_id(async_session):
    repo = await insert_repo(async_session)
    return repo.id


async def test_a_c_are_not_merged_through_b(async_session, repo_id):
    """The transitivity case: B bridges A and C, and A and C stay apart."""
    store = _store()
    a, b, c = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Alpha @a", "source": "pr"},
            {"title": "Bravo @b", "source": "comment"},
            {"title": "Charlie @c", "source": "comment"},
        ],
    )
    plan = await plan_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    folded_into = {
        folded_id: cluster.canonical_id
        for cluster in plan.clusters
        for folded_id, _t, _s in cluster.folded
    }
    # B folds into exactly one of its two neighbours, never both, and the
    # neighbour it did not fold into is left as its own record.
    assert len(folded_into) == 1
    assert b.id in folded_into
    assert folded_into[b.id] in {a.id, c.id}
    assert a.id not in folded_into and c.id not in folded_into


async def test_source_rank_then_completeness_picks_the_canonical(async_session, repo_id):
    store = _store()
    _thin, full, low = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Thin @a", "source": "pr"},
            {"title": "Full @a", "source": "pr", "decision": "Do the thing", "rationale": "Why"},
            {"title": "Low @a", "source": "comment", "decision": "Do the thing too"},
        ],
    )
    plan = await plan_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    assert len(plan.clusters) == 1
    # ``pr`` outranks ``comment``, so the comment record cannot be canonical
    # even though it has a body; between the two ``pr`` records the one that
    # says something wins.
    assert plan.clusters[0].canonical_id == full.id
    assert low.id in {i for i, _t, _s in plan.clusters[0].folded}


async def test_fold_moves_evidence_without_downgrading_the_canonical(async_session, repo_id):
    store = _store()
    canonical, duplicate = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Drop @a", "source": "comment"},
        ],
    )
    # Same natural key on both, stronger on the canonical.
    async_session.add_all(
        [
            DecisionEvidence(
                decision_id=canonical.id,
                source="pr",
                source_rank=7,
                evidence_file="a.py",
                evidence_commit="c1",
                source_quote="q",
                confidence=0.8,
                verification="exact",
            ),
            DecisionEvidence(
                decision_id=duplicate.id,
                source="pr",
                source_rank=7,
                evidence_file="a.py",
                evidence_commit="c1",
                source_quote="q",
                confidence=0.2,
                verification="fuzzy",
            ),
            DecisionEvidence(
                decision_id=duplicate.id,
                source="comment",
                source_rank=3,
                evidence_file="b.py",
                evidence_commit="c2",
                source_quote="q2",
                confidence=0.4,
                verification="exact",
            ),
        ]
    )
    await async_session.flush()

    await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    rows = (
        (
            await async_session.execute(
                select(DecisionEvidence).where(DecisionEvidence.decision_id == canonical.id)
            )
        )
        .scalars()
        .all()
    )
    by_key = {(r.source, r.evidence_file): r for r in rows}
    # The genuinely new source moved; the colliding one did not overwrite.
    assert ("comment", "b.py") in by_key
    assert by_key[("pr", "a.py")].verification == "exact"
    assert by_key[("pr", "a.py")].confidence == pytest.approx(0.8)


async def test_a_fold_moves_the_canonical_to_the_checkable_noun(
    async_session, repo_id
):
    """An agreement that absorbs a record about the code is about the code too.

    The union gives the canonical the folded record's files, and a record that
    names files is one being checked against them, so the noun has to follow or
    the canonical claims to govern the repository while naming part of it.
    """
    store = _store()
    canonical, _duplicate = await _seed(
        async_session,
        store,
        repo_id,
        [
            # session outranks comment, so the agreement is the canonical and
            # the record about the code is what folds into it.
            {
                "title": "Never commit to main @a",
                "kind": "agreement",
                "source": "session",
            },
            {
                "title": "Never commit to trunk @a",
                "source": "comment",
                "files": ["src/app.py"],
            },
        ],
    )
    await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    survivor = await async_session.get(DecisionRecord, canonical.id)
    assert survivor.kind == "architectural"
    assert json.loads(survivor.affected_files_json) == ["src/app.py"]


async def test_folded_id_still_resolves_and_its_files_survive(async_session, repo_id):
    store = _store()
    canonical, duplicate = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body", "files": ["kept.py"]},
            {"title": "Drop @a", "source": "comment", "files": ["only_on_dup.py"]},
        ],
    )
    await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    assert await async_session.get(DecisionRecord, duplicate.id) is None
    assert await resolve_decision_id(async_session, duplicate.id) == canonical.id

    survivor = await async_session.get(DecisionRecord, canonical.id)
    assert json.loads(survivor.affected_files_json) == ["kept.py", "only_on_dup.py"]
    nodes = {
        row.node_id
        for row in (
            (
                await async_session.execute(
                    select(DecisionNodeLink).where(
                        DecisionNodeLink.decision_id == canonical.id
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    # The duplicate's governed file keeps a decision, rather than losing all
    # coverage when its record goes.
    assert "only_on_dup.py" in nodes


async def test_the_folded_vector_goes_and_the_canonical_stays(async_session, repo_id):
    store = _store()
    canonical, duplicate = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Drop @a", "source": "comment"},
        ],
    )
    await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    present = await store.list_page_ids()
    assert f"{DECISION_VECTOR_PREFIX}{canonical.id}" in present
    assert f"{DECISION_VECTOR_PREFIX}{duplicate.id}" not in present


async def test_second_run_folds_nothing(async_session, repo_id):
    store = _store()
    await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Drop @a", "source": "comment"},
        ],
    )
    first = await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)
    assert first.folded_count == 1

    second = await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)
    assert second.folded_count == 0
    assert second.clusters == []


async def test_an_accepted_decision_is_left_alone(async_session, repo_id):
    store = _store()
    accepted, duplicate = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Governs @a", "source": "pr", "decision": "Body"},
            {"title": "Echo @a", "source": "pr", "decision": "Body"},
        ],
    )
    await accept_decision(
        async_session,
        accepted,
        accepter="someone",
        reason="because",
        scope=["x.py"],
        evidence=["e"],
    )
    await async_session.flush()

    plan = await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    # Folding into something that governs is a merge, and merge_candidate owns
    # that; neither record may be touched here.
    assert plan.folded_count == 0
    assert await async_session.get(DecisionRecord, duplicate.id) is not None


async def test_a_keyless_store_says_so_instead_of_saying_clean(async_session, repo_id):
    from repowise.core.providers.embedding.base import KeylessEmbedder

    store = _store()
    await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Drop @a", "source": "comment"},
        ],
    )
    keyless = InMemoryVectorStore(KeylessEmbedder())
    plan = await plan_dedupe(async_session, repo_id, vector_store=keyless, tau=TAU)

    # A store that could not be asked must not read as a store with nothing
    # to fold: the report turns on this flag, not on the zero.
    assert plan.degraded is True
    assert plan.clusters == []
    assert "nothing could be examined" in render_plan(plan)


async def test_a_record_a_reviewer_flagged_is_not_folded(async_session, repo_id):
    from repowise.core.persistence.crud.authority import request_split

    store = _store()
    _canonical, flagged = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Bundles two @a", "source": "pr", "decision": "Body and more"},
        ],
    )
    # request_split leaves status at "proposed", so only the review row says a
    # person has judged this record.
    await request_split(async_session, flagged, reason="two choices in one")

    plan = await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    assert plan.folded_count == 0
    assert await async_session.get(DecisionRecord, flagged.id) is not None


async def test_an_unaccepted_active_record_is_still_a_candidate(async_session, repo_id):
    store = _store()
    _canonical, legacy = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Legacy @a", "source": "comment", "status": "active"},
        ],
    )
    plan = await plan_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    # Authority lives in the acceptance log, not the status column, so an
    # "active" row nobody accepted is a duplicate candidate like any other.
    assert plan.eligible == 2
    assert legacy.id in {i for c in plan.clusters for i, _t, _s in c.folded}


async def test_a_stale_plan_cannot_fold_a_record_since_accepted(async_session, repo_id):
    store = _store()
    canonical, duplicate = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Drop @a", "source": "pr", "decision": "Body too"},
        ],
    )
    plan = await plan_dedupe(async_session, repo_id, vector_store=store, tau=TAU)
    assert plan.folded_count == 1

    # Someone accepts the record the stale plan wants to delete.
    target = duplicate if plan.clusters[0].canonical_id == canonical.id else canonical
    await accept_decision(
        async_session,
        target,
        accepter="someone",
        reason="because",
        scope=["x.py"],
        evidence=["e"],
    )
    await async_session.flush()

    applied = await apply_dedupe(async_session, repo_id, vector_store=store, plan=plan)

    assert applied.folded_count == 0
    assert await async_session.get(DecisionRecord, target.id) is not None


async def test_an_ineligible_record_is_not_dragged_into_a_cluster(async_session, repo_id):
    """Three mutually-close records, the highest-ranking one flagged.

    The exclusion tests each leave fewer than two foldable records, so they
    short-circuit before a cluster is formed. This one forms a real cluster
    around the flagged record and pins that it still stays out — and because
    ``cli`` outranks ``pr``, it would have been the canonical if it were
    eligible, which is the strongest form of the check.
    """
    from repowise.core.persistence.crud.authority import request_split

    store = _store()
    first, flagged, third = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "One @a", "source": "pr", "decision": "Body"},
            {"title": "Two @a", "source": "cli", "decision": "Body and more"},
            {"title": "Three @a", "source": "pr", "decision": "Body again"},
        ],
    )
    await request_split(async_session, flagged, reason="two choices in one")

    applied = await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    assert applied.folded_count == 1
    assert await async_session.get(DecisionRecord, flagged.id) is not None
    touched = {c.canonical_id for c in applied.clusters} | {
        i for c in applied.clusters for i, _t, _s in c.folded
    }
    assert flagged.id not in touched
    assert touched == {first.id, third.id}


async def test_a_superseded_record_keeps_its_lineage(async_session, repo_id):
    store = _store()
    _canonical, retired = await _seed(
        async_session,
        store,
        repo_id,
        [
            {"title": "Keep @a", "source": "pr", "decision": "Body"},
            {"title": "Retired @a", "source": "pr", "status": "superseded"},
        ],
    )
    plan = await apply_dedupe(async_session, repo_id, vector_store=store, tau=TAU)

    assert plan.folded_count == 0
    assert await async_session.get(DecisionRecord, retired.id) is not None

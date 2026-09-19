"""A commit footprint keeps its files and stops answering per-file questions.

The defect these pin, measured 2026-09-19 over 41 labelled file/decision pairs:
``get_why`` on a file answered with decisions about something else 22% of the
time, and it tracked scope breadth and nothing else. The cause is that the
``pr`` and ``git_archaeology`` miners read one decision out of one commit body
and then take that commit's whole file list, so a decision mined from a
42-file refactor claims every file the refactor touched.

The canonical case, from the dev store: ``search_digest.py``, whose whole job
is compacting Grep output, carried five decisions about *collapsing repeated
file reads* -- a mechanism in a sibling module -- because all five were mined
from one 38-file commit that happened to touch it.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from repowise.core.analysis.decisions.scope import (
    SCOPE_BASIS_FOOTPRINT,
    SCOPE_BASIS_STATED,
)
from repowise.core.persistence.crud import bulk_upsert_decisions, update_decision_metadata
from repowise.core.persistence.decision_graph import (
    get_governed_nodes,
    get_governing_decisions,
)
from repowise.core.persistence.decision_migration import backfill_scope_basis
from repowise.core.persistence.models import DecisionNodeLink, DecisionRecord
from tests.unit.persistence.helpers import insert_repo

#: The mechanism file and the bystander, as in the real commit.
_MECHANISM = "packages/cli/src/repowise/cli/commands/augment_cmd/read_state.py"
_BYSTANDER = "packages/cli/src/repowise/cli/commands/augment_cmd/search_digest.py"
_COMMIT_FILES = [_MECHANISM, _BYSTANDER] + [
    f"packages/core/src/mod{i:02d}.py" for i in range(36)
]


def _decision(
    title: str, *, files: list[str], scope_basis: str = "", source: str = "pr"
) -> dict:
    return {
        "title": title,
        "decision": f"{title} body",
        "rationale": "measured",
        "source": source,
        "status": "active",
        "affected_files": files,
        "affected_modules": [],
        "scope_basis": scope_basis,
        "evidence_file": None,
        "confidence": 0.8,
        "verification": "exact",
        "source_quote": title,
    }


async def test_a_footprint_writes_no_file_links(async_session):
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision(
                "Collapse unchanged hook re-reads",
                files=_COMMIT_FILES,
                scope_basis=SCOPE_BASIS_FOOTPRINT,
            )
        ],
    )
    did = ids[0]

    # The bystander must not be told this governs it. Nor must the mechanism:
    # the record carries no evidence naming either one.
    assert await get_governing_decisions(async_session, repo.id, _BYSTANDER) == []
    assert await get_governing_decisions(async_session, repo.id, _MECHANISM) == []
    assert [link.link_type for link in await get_governed_nodes(async_session, did)] == []


async def test_a_footprint_keeps_its_files_on_the_record(async_session):
    """Provenance survives. Staleness and the origin story both read this."""
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Wide rule", files=_COMMIT_FILES, scope_basis=SCOPE_BASIS_FOOTPRINT)],
    )
    rec = await async_session.get(DecisionRecord, ids[0])
    assert json.loads(rec.affected_files_json) == _COMMIT_FILES
    assert rec.scope_basis == SCOPE_BASIS_FOOTPRINT


async def test_a_narrow_record_still_binds(async_session):
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Pin the read state", files=[_MECHANISM], source="inline_marker")],
    )
    governing = await get_governing_decisions(async_session, repo.id, _MECHANISM)
    assert [d.id for d in governing] == ids


async def test_a_footprint_links_no_modules_either(async_session):
    """Its module list is the directories of the same unjustified file list.

    A commit-wide record does sound like a claim about the areas it touched,
    but a 42-file commit yields a dozen directories on exactly the evidence
    that made the file list wrong.
    """
    repo = await insert_repo(async_session)
    payload = _decision(
        "Wide rule", files=_COMMIT_FILES, scope_basis=SCOPE_BASIS_FOOTPRINT
    )
    payload["affected_modules"] = ["packages/core/src"]
    ids = await bulk_upsert_decisions(async_session, repo.id, [payload])
    assert await get_governed_nodes(async_session, ids[0]) == []


# ---------------------------------------------------------------------------
# The backfill, for records written before the basis existed
# ---------------------------------------------------------------------------


async def test_backfill_marks_a_legacy_wide_pr_record_and_drops_its_file_links(
    async_session,
):
    repo = await insert_repo(async_session)
    # Written the way the old code wrote it: no basis, whole commit list.
    ids = await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Legacy wide", files=_COMMIT_FILES)]
    )
    did = ids[0]
    assert await get_governing_decisions(async_session, repo.id, _BYSTANDER) != []

    assert await backfill_scope_basis(async_session, repo.id) == 1

    rec = await async_session.get(DecisionRecord, did)
    assert rec.scope_basis == SCOPE_BASIS_FOOTPRINT
    assert json.loads(rec.affected_files_json) == _COMMIT_FILES
    assert await get_governing_decisions(async_session, repo.id, _BYSTANDER) == []


async def test_backfill_leaves_a_narrow_commit_record_alone(async_session):
    repo = await insert_repo(async_session)
    files = [f"pkg/m{i}.py" for i in range(4)]
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Narrow", files=files)]
    )

    assert await backfill_scope_basis(async_session, repo.id) == 0
    assert await get_governing_decisions(async_session, repo.id, files[0]) != []


async def test_backfill_leaves_a_wide_record_from_another_source_alone(async_session):
    """Only the two miners that never chose a file are repaired.

    A session-mined record names files the session touched, so a wide one is a
    wide claim rather than a footprint, and demoting it here would be guessing.
    """
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Wide session rule", files=_COMMIT_FILES, source="session")],
    )
    assert await backfill_scope_basis(async_session, repo.id) == 0
    assert await get_governing_decisions(async_session, repo.id, _BYSTANDER) != []


async def test_backfill_is_idempotent(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Legacy wide", files=_COMMIT_FILES)]
    )
    assert await backfill_scope_basis(async_session, repo.id) == 1
    assert await backfill_scope_basis(async_session, repo.id) == 0


async def test_backfill_does_not_touch_a_basis_somebody_already_set(async_session):
    """``decision confirm --scope`` writes a scope by hand; it stays."""
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Confirmed wide", files=_COMMIT_FILES, scope_basis="stated")],
    )
    assert await backfill_scope_basis(async_session, repo.id) == 0
    rec = await async_session.get(DecisionRecord, ids[0])
    assert rec.scope_basis == "stated"


async def test_backfill_drops_every_link_the_record_had(async_session):
    """Matching what the write path would now produce for the same record."""
    repo = await insert_repo(async_session)
    payload = _decision("Legacy wide", files=_COMMIT_FILES)
    payload["affected_modules"] = ["packages/core/src"]
    ids = await bulk_upsert_decisions(async_session, repo.id, [payload])

    await backfill_scope_basis(async_session, repo.id)

    rows = (
        (
            await async_session.execute(
                select(DecisionNodeLink).where(DecisionNodeLink.decision_id == ids[0])
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


# ---------------------------------------------------------------------------
# A scope a person states outranks a mined one
# ---------------------------------------------------------------------------


async def test_stating_a_scope_by_hand_makes_a_footprint_bind_again(async_session):
    """``decision confirm --scope`` is how a wide record gets a real scope.

    Accepting the new files and then ignoring them on every per-file surface
    would be the worst of both.
    """
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision(
                "Collapse unchanged hook re-reads",
                files=_COMMIT_FILES,
                scope_basis=SCOPE_BASIS_FOOTPRINT,
            )
        ],
    )
    assert await get_governing_decisions(async_session, repo.id, _MECHANISM) == []

    await update_decision_metadata(async_session, ids[0], affected_files=[_MECHANISM])

    rec = await async_session.get(DecisionRecord, ids[0])
    assert rec.scope_basis == SCOPE_BASIS_STATED
    assert json.loads(rec.affected_files_json) == [_MECHANISM]


async def test_the_backfill_never_re_marks_a_stated_scope(async_session):
    """The row still reads as a wide ``pr`` record, and must be left alone.

    Without this the next index walks a hand-narrowed scope back to a
    footprint, because the repair only looks at source and file count.
    """
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Legacy wide", files=_COMMIT_FILES)]
    )
    await update_decision_metadata(async_session, ids[0], affected_files=_COMMIT_FILES)

    assert await backfill_scope_basis(async_session, repo.id) == 0
    rec = await async_session.get(DecisionRecord, ids[0])
    assert rec.scope_basis == SCOPE_BASIS_STATED


async def test_patching_only_modules_leaves_the_basis_alone(async_session):
    """The basis describes the file list, so a module-only patch says nothing."""
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Wide", files=_COMMIT_FILES, scope_basis=SCOPE_BASIS_FOOTPRINT)],
    )
    await update_decision_metadata(async_session, ids[0], affected_modules=["pkg"])

    rec = await async_session.get(DecisionRecord, ids[0])
    assert rec.scope_basis == SCOPE_BASIS_FOOTPRINT


async def test_accepting_with_a_corrected_scope_makes_the_record_bind(async_session):
    """``decision confirm --scope`` runs through ``accept_decision``.

    This is the path a person actually uses to give a wide mined record a real
    scope, and the one where storing the corrected files and then ignoring
    them would be least visible.
    """
    from repowise.core.persistence.crud.authority import accept_decision

    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision(
                "Collapse unchanged hook re-reads",
                files=_COMMIT_FILES,
                scope_basis=SCOPE_BASIS_FOOTPRINT,
            )
        ],
    )
    rec = await async_session.get(DecisionRecord, ids[0])
    rec.rationale = "because the reads were measured redundant"

    await accept_decision(
        async_session,
        rec,
        accepter="tester",
        scope=[_MECHANISM],
        evidence=["abc1234"],
    )

    refreshed = await async_session.get(DecisionRecord, ids[0])
    assert refreshed.scope_basis == SCOPE_BASIS_STATED
    assert json.loads(refreshed.affected_files_json) == [_MECHANISM]


async def test_accepting_without_a_scope_leaves_the_basis_alone(async_session):
    """No scope argument means no claim about files, so nothing changes."""
    from repowise.core.persistence.crud.authority import accept_decision

    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Narrow", files=[_MECHANISM], source="inline_marker")],
    )
    rec = await async_session.get(DecisionRecord, ids[0])
    rec.rationale = "measured"
    rec.scope_basis = SCOPE_BASIS_FOOTPRINT

    await accept_decision(
        async_session, rec, accepter="tester", evidence=["abc1234"]
    )

    refreshed = await async_session.get(DecisionRecord, ids[0])
    assert refreshed.scope_basis == SCOPE_BASIS_FOOTPRINT

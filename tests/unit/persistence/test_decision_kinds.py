"""A working agreement is a different noun from an architectural decision.

The contract under test: an agreement governs the repository rather than part
of it, so naming no file is what it *is* rather than a gap in it. Before the
split that made it unacceptable outright, because the acceptance contract and
the ``scope_json`` CHECK both demand a scope, and an agreement has no file to
name. Nothing here may loosen that contract for an architectural decision,
which still has to say what it governs.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from repowise.core.analysis.decisions.kinds import classify_kind
from repowise.core.analysis.decisions.lifecycle import (
    AGREEMENT_KIND,
    AGREEMENT_SCOPE,
    ARCHITECTURAL_KIND,
    effective_currency,
)
from repowise.core.analysis.decisions.manifest import manifest_path
from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import (
    AcceptanceRefusedError,
    accept_decision,
    candidate_review_signals,
    count_decisions_by_lane,
    current_currency,
)
from repowise.core.persistence.crud.decisions import _extraction_kind
from repowise.core.persistence.decision_manifest import export_manifest, import_manifest
from repowise.core.persistence.models import DecisionAcceptance, DecisionRecord
from tests.unit.persistence.helpers import insert_repo


def _dict(title: str, **overrides) -> dict:
    base = {
        "title": title,
        "decision": f"{title}: do the thing",
        "rationale": "because the alternative was measured slower",
        "source": "session",
        "status": "proposed",
        "affected_files": ["src/app.py"],
        "evidence_file": "src/app.py",
        "confidence": 0.7,
        "verification": "exact",
        "source_quote": f"{title}: do the thing",
    }
    base.update(overrides)
    return base


async def _seed(session, repo_id: str, *payloads: dict) -> list[DecisionRecord]:
    await bulk_upsert_decisions(session, repo_id, list(payloads))
    rows = (
        (
            await session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    by_title = {r.title: r for r in rows}
    return [by_title[p["title"]] for p in payloads]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,decision",
    [
        ("Never commit to main", "Do not commit to the main branch."),
        ("Never stage all changes", "Do not use `git add -A`."),
        ("Omit Co-Authored-By lines", "Do not include Co-Authored-By lines."),
        ("Never run Ruff format", "Do not run `ruff format` on the codebase."),
        ("Only read the main checkout", "Do not modify the main checkout."),
    ],
)
def test_an_act_of_conducting_work_is_an_agreement(title, decision):
    assert classify_kind(title, decision, source="session") == AGREEMENT_KIND


@pytest.mark.parametrize(
    "title,decision",
    [
        ("Keep `why` free of LLM calls", "Do not add an LLM call to the why flow."),
        ("Keep the health layer deterministic", "Do not make model calls there."),
        ("Keep SnapshotResponse narrow", "Do not widen SnapshotResponse."),
        ("Do not import Next", "Do not add `next` imports."),
    ],
)
def test_a_claim_about_the_code_stays_architectural(title, decision):
    """These name no file either, which is exactly why scope cannot classify."""
    assert classify_kind(title, decision, source="session") == ARCHITECTURAL_KIND


def test_only_a_prose_source_is_asked():
    """The same words mean something else in an artifact written about the code.

    A comment saying "keep package attribution in one place" is about attributing
    code to packages, not about who signs a commit, so the question is not put to
    it at all rather than answered badly.
    """
    title, decision = "Never stage all changes", "Do not use `git add -A`."
    assert classify_kind(title, decision, source="session") == AGREEMENT_KIND
    assert classify_kind(title, decision, source="comment") == ARCHITECTURAL_KIND
    assert classify_kind(title, decision, source="pr") == ARCHITECTURAL_KIND
    # An unnamed caller gets the kind that keeps being checked, per the module's
    # asymmetry: the default must not be the permissive one.
    assert classify_kind(title, decision) == ARCHITECTURAL_KIND


# ---------------------------------------------------------------------------
# The acceptance contract
# ---------------------------------------------------------------------------


async def test_an_agreement_naming_no_file_can_be_accepted(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Never commit to main", affected_files=[], kind=AGREEMENT_KIND),
    )
    assert rec.kind == AGREEMENT_KIND

    _, scope_unresolved = candidate_review_signals(rec)
    assert scope_unresolved is False

    await accept_decision(async_session, rec, accepter="tester")
    acceptance = (
        await async_session.execute(
            select(DecisionAcceptance).where(DecisionAcceptance.decision_id == rec.id)
        )
    ).scalar_one()
    # Stated, not exempted: the CHECK still refuses an empty scope, and what is
    # recorded is the scope an agreement actually has.
    assert acceptance.scope_json == f'["{AGREEMENT_SCOPE}"]'


async def test_an_architectural_record_naming_no_file_is_still_refused(async_session):
    """The no-regression half. The contract did not get looser for a decision."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Use Redis", affected_files=[], kind=ARCHITECTURAL_KIND),
    )
    _, scope_unresolved = candidate_review_signals(rec)
    assert scope_unresolved is True

    with pytest.raises(AcceptanceRefusedError) as exc:
        await accept_decision(async_session, rec, accepter="tester")
    assert "no scope" in str(exc.value)


# ---------------------------------------------------------------------------
# Currency. An agreement has no files, so neither question can be put to it.
# ---------------------------------------------------------------------------


def test_a_repo_wide_record_is_never_uncheckable_or_needs_review():
    assert (
        effective_currency("active", has_scope=False, staleness=0.0, repo_wide=True)
        == "active"
    )
    assert (
        effective_currency("active", has_scope=True, staleness=1.0, repo_wide=True)
        == "active"
    )
    # Unchanged for everything else.
    assert effective_currency("active", has_scope=False, staleness=0.0) == "uncheckable"
    assert effective_currency("active", has_scope=True, staleness=1.0) == "needs_review"


async def test_an_accepted_agreement_is_active_despite_inherited_staleness(
    async_session,
):
    """Staleness is measured over files. An agreement has none, so the score
    it carries from before the split says nothing about it."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Never run ruff format", affected_files=[], kind=AGREEMENT_KIND),
    )
    rec.staleness_score = 1.0
    await async_session.flush()

    await accept_decision(async_session, rec, accepter="tester")
    assert await current_currency(async_session, rec) == "active"


async def test_the_lane_counts_agree_with_the_currency(async_session):
    """Two implementations of the same partition, and they must not disagree."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Pause before pushing", affected_files=[], kind=AGREEMENT_KIND),
    )
    rec.staleness_score = 1.0
    await async_session.flush()
    await accept_decision(async_session, rec, accepter="tester")

    counts = await count_decisions_by_lane(async_session, repo.id)
    assert counts["active"] == 1
    assert counts["uncheckable"] == 0
    assert counts["needs_review"] == 0
    assert await current_currency(async_session, rec) == "active"


async def test_an_agreement_that_names_files_is_still_checked(async_session):
    """The noun says a record *may* name nothing, not that files it does name
    should be ignored. The classifier is a regex with a measured false-positive
    rate, so a misclassified record must not fall out of staleness checking."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Never stage all changes", affected_files=["src/app.py"]),
    )
    rec.kind = AGREEMENT_KIND
    rec.staleness_score = 1.0
    await async_session.flush()

    await accept_decision(async_session, rec, accepter="tester")
    assert await current_currency(async_session, rec) == "needs_review"

    counts = await count_decisions_by_lane(async_session, repo.id)
    assert counts["needs_review"] == 1
    assert counts["active"] == 0


async def test_the_five_lanes_still_partition_with_an_agreement_present(async_session):
    """The property the lanes actually promise: every record in exactly one."""
    repo = await insert_repo(async_session)
    agreement, decision, _candidate = await _seed(
        async_session,
        repo.id,
        _dict("Never commit to main", affected_files=[], kind=AGREEMENT_KIND),
        _dict("Use Redis"),
        _dict("Use Postgres"),
    )
    await accept_decision(async_session, agreement, accepter="tester")
    await accept_decision(async_session, decision, accepter="tester")

    counts = await count_decisions_by_lane(async_session, repo.id)
    lanes = ["active", "candidates", "needs_review", "uncheckable", "history"]
    assert sum(counts[lane] for lane in lanes) == counts["total"] == 3
    # Which lane, not just how many: the sum alone cannot tell this apart from
    # a partition where the agreement fell into candidates or uncheckable.
    assert counts["active"] == 2
    assert counts["candidates"] == 1
    assert counts["uncheckable"] == 0
    assert counts["needs_review"] == 0
    assert counts["history"] == 0


# ---------------------------------------------------------------------------
# The noun survives the paths that rewrite a record
# ---------------------------------------------------------------------------


async def test_re_extraction_does_not_change_the_noun(async_session):
    """The noun is decided at creation. Re-extraction must not revisit it, in
    either direction, because this path runs on accepted records too and
    flipping the noun there changes what a record governs behind the person who
    accepted it."""
    repo = await insert_repo(async_session)
    (agreement,) = await _seed(
        async_session,
        repo.id,
        _dict("Never commit to main", affected_files=[], kind=AGREEMENT_KIND),
    )
    (decision,) = await _seed(async_session, repo.id, _dict("Use Redis"))
    await accept_decision(async_session, decision, accepter="tester")

    # A corroborating source defaults the field rather than deciding it.
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_dict("Never commit to main", affected_files=[], source="pr")],
    )
    # And a re-mined session record whose text now trips the classifier must
    # not pull an accepted decision out of checking.
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_dict("Use Redis", kind=AGREEMENT_KIND)],
    )

    await async_session.refresh(agreement)
    await async_session.refresh(decision)
    assert agreement.kind == AGREEMENT_KIND
    assert decision.kind == ARCHITECTURAL_KIND


def test_a_junk_kind_is_normalized_rather_than_stored():
    """Nothing enforces the vocabulary at the database, so the write path does."""
    assert _extraction_kind("Agreement") == ARCHITECTURAL_KIND
    assert _extraction_kind("") == ARCHITECTURAL_KIND
    assert _extraction_kind(None) == ARCHITECTURAL_KIND
    assert _extraction_kind(AGREEMENT_KIND) == AGREEMENT_KIND


async def test_an_agreement_round_trips_through_the_manifest(tmp_path, async_session):
    """The committed file is the recovery path, so it must not turn the scope
    marker into a file path or lose the noun on the way back in."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Never commit to main", affected_files=[], kind=AGREEMENT_KIND),
    )
    await accept_decision(async_session, rec, accepter="tester")

    await export_manifest(async_session, repo.id, tmp_path)
    text = manifest_path(tmp_path).read_text(encoding="utf-8")
    assert "kind: agreement" in text

    await async_session.delete(rec)
    await async_session.flush()

    await import_manifest(async_session, repo.id, tmp_path)
    restored = (
        await async_session.execute(
            select(DecisionRecord).where(DecisionRecord.title == "Never commit to main")
        )
    ).scalar_one()
    assert restored.kind == AGREEMENT_KIND
    # The marker is what it governs, not a file. Storing it here would put a
    # path that cannot exist in front of staleness, where it counts as changed.
    assert json.loads(restored.affected_files_json) == []
    assert await current_currency(async_session, restored) == "active"


async def test_changing_the_noun_in_the_file_reaches_the_store(tmp_path, async_session):
    """The reaffirm branch, not the create one. If the file cannot express a
    change to the noun, the committed file cannot correct a misclassification,
    which for an already-accepted record is the only correction there is."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Never commit to main", affected_files=[], kind=AGREEMENT_KIND),
    )
    await accept_decision(async_session, rec, accepter="tester")
    await export_manifest(async_session, repo.id, tmp_path)

    path = manifest_path(tmp_path)
    # A person decides it was really a claim about the code, and says so by
    # naming what it governs instead of the repository.
    path.write_text(
        path.read_text(encoding="utf-8")
            .replace("  kind: agreement\n", "")
            .replace(f"  - {AGREEMENT_SCOPE}\n", "  - src/app.py\n"),
        encoding="utf-8",
    )

    outcome = await import_manifest(async_session, repo.id, tmp_path)
    assert rec.id in outcome.reaffirmed
    await async_session.refresh(rec)
    assert rec.kind == ARCHITECTURAL_KIND
    assert json.loads(rec.affected_files_json) == ["src/app.py"]


async def test_a_noun_only_edit_is_not_read_as_unchanged(tmp_path, async_session):
    """The scope is identical and only the noun moves, so nothing but the kind
    comparison can notice. Without it the committed file silently cannot
    correct a misclassification on a record that names real files."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Never stage all changes", affected_files=["src/app.py"]),
    )
    await accept_decision(async_session, rec, accepter="tester")
    await export_manifest(async_session, repo.id, tmp_path)

    path = manifest_path(tmp_path)
    assert "kind:" not in path.read_text(encoding="utf-8")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "  source: session\n", "  source: session\n  kind: agreement\n"
        ),
        encoding="utf-8",
    )

    outcome = await import_manifest(async_session, repo.id, tmp_path)
    assert rec.id in outcome.reaffirmed
    await async_session.refresh(rec)
    assert rec.kind == AGREEMENT_KIND


async def test_the_marker_alone_does_not_accept_a_decision(tmp_path, async_session):
    """The marker is a scope for an agreement and nothing for a decision. A
    file that names it without saying which noun must not produce an accepted
    record governing no code, which is what the contract exists to refuse."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        _dict("Never commit to main", affected_files=[], kind=AGREEMENT_KIND),
    )
    await accept_decision(async_session, rec, accepter="tester")
    await export_manifest(async_session, repo.id, tmp_path)

    path = manifest_path(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("  kind: agreement\n", ""),
        encoding="utf-8",
    )
    await async_session.delete(rec)
    await async_session.flush()

    outcome = await import_manifest(async_session, repo.id, tmp_path)
    assert [reason for _id, reason in outcome.skipped] == ["no scope recorded"]
    assert outcome.created == []

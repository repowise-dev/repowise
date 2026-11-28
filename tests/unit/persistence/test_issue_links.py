"""The issue link store: replace on a complete fetch, add on a partial one, name what moved."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.issues import StoredLink
from repowise.core.persistence.crud.issue_links import (
    cite_prior_defect_issues,
    fix_issue_quotes,
    load_tracker_fixes,
    store_issue_links,
)
from repowise.core.persistence.models import FixEvent, GitCommit, IssueLink
from repowise.core.pipeline.incremental.commits import reconcile_issue_fix_flags
from tests.unit.persistence.helpers import insert_repo

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _link(sha: str, key: str, kind: str = "bug", title: str = "") -> StoredLink:
    return StoredLink(sha=sha, key=key, type=kind, status="Done", title=title, url="", basis="")


async def _stored(session) -> set[tuple[str, str, str, str]]:
    rows = (await session.execute(select(IssueLink))).scalars().all()
    return {(r.commit_sha, r.issue_key, r.issue_type, r.source) for r in rows}


@pytest.mark.asyncio
async def test_a_complete_fetch_replaces_its_own_links_on_the_commits_it_covered(async_session):
    repo = await insert_repo(async_session)
    await store_issue_links(
        async_session, repo.id, [_link(A, "ENG-1"), _link(B, "ENG-2")], source="t"
    )
    await store_issue_links(async_session, repo.id, [_link(C, "OPS-9")], source="other")

    result = await store_issue_links(
        async_session, repo.id, [_link(A, "ENG-1", "feature")], source="t", covered_shas=[A, B, C]
    )

    assert await _stored(async_session) == {
        (A, "ENG-1", "feature", "t"),
        # Another source's link on a covered commit stays.
        (C, "OPS-9", "bug", "other"),
    }
    assert result == {"stored": 1, "removed": 1, "defect_changed": [A, B]}


@pytest.mark.asyncio
async def test_a_partial_fetch_only_adds(async_session):
    repo = await insert_repo(async_session)
    await store_issue_links(async_session, repo.id, [_link(A, "ENG-1")], source="t")

    result = await store_issue_links(async_session, repo.id, [_link(B, "ENG-2")], source="t")

    assert {k for _, k, _, _ in await _stored(async_session)} == {"ENG-1", "ENG-2"}
    assert result["removed"] == 0
    assert await load_tracker_fixes(async_session, repo.id) == {A: {"ENG-1"}, B: {"ENG-2"}}


@pytest.mark.asyncio
async def test_quotes_name_the_issues_behind_a_files_counted_fixes(async_session):
    repo = await insert_repo(async_session)
    at = datetime(2026, 7, 1, tzinfo=UTC)
    for sha, shape in ((A, "code_fix"), (B, "code_fix"), (C, "test_only")):
        async_session.add(
            FixEvent(
                repository_id=repo.id,
                fix_sha=sha,
                file_path="cart.py",
                shape_kind=shape,
                old_ranges_json="[]",
                committed_at=at,
            )
        )
    await async_session.flush()
    await store_issue_links(
        async_session,
        repo.id,
        [
            _link(A, "ENG-1", title="Checkout fails on an empty cart"),
            _link(B, "ENG-2", "feature"),
            _link(C, "ENG-3"),  # a fix that changed no production code is not counted
        ],
        source="t",
    )

    assert await fix_issue_quotes(async_session, repo.id, ["cart.py", "other.py"]) == {
        "cart.py": ["ENG-1 (bug): Checkout fails on an empty cart"]
    }


def _commit(repo_id: str, sha: str, subject: str, is_fix: bool) -> GitCommit:
    return GitCommit(
        repository_id=repo_id,
        sha=sha,
        author_name="a",
        author_email="a@x",
        subject=subject,
        lines_added=1,
        lines_deleted=0,
        files_changed=1,
        dirs_changed=1,
        subsystems_changed=1,
        entropy=0.0,
        is_fix=is_fix,
        author_experience=0,
        change_risk_score=1.5,
        change_risk_level="low",
    )


@pytest.mark.asyncio
async def test_is_fix_adds_linked_bugs_and_never_drops_a_keyword_fix(async_session):
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            _commit(repo.id, A, "Handle empty carts (ENG-1)", False),
            _commit(repo.id, B, "fix crash on login", True),
            _commit(repo.id, C, "Add export (ENG-3)", True),  # flagged by a link since retyped
        ]
    )
    await async_session.flush()
    await store_issue_links(
        async_session,
        repo.id,
        [_link(A, "ENG-1"), _link(B, "ENG-2", "task"), _link(C, "ENG-3", "feature")],
        source="t",
    )

    await reconcile_issue_fix_flags(async_session, repo.id)

    flags = {c.sha: c.is_fix for c in (await async_session.execute(select(GitCommit))).scalars()}
    assert flags == {A: True, B: True, C: False}


@pytest.mark.asyncio
async def test_with_no_links_left_a_linked_flag_goes_and_the_rule_stays(async_session, monkeypatch):
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            _commit(repo.id, A, "Handle empty carts (ENG-1)", False),
            _commit(repo.id, B, "fix crash on login", True),
        ]
    )
    await async_session.flush()
    await store_issue_links(async_session, repo.id, [_link(A, "ENG-1")], source="t")
    await reconcile_issue_fix_flags(async_session, repo.id)

    # The last link goes: a full fetch that no longer finds it.
    await store_issue_links(async_session, repo.id, [], source="t", covered_shas=[A, B])
    await reconcile_issue_fix_flags(async_session, repo.id)
    flags = {c.sha: c.is_fix for c in (await async_session.execute(select(GitCommit))).scalars()}
    assert flags == {A: False, B: True}

    # Rows that match the rule are never rewritten.
    from repowise.core.persistence import crud

    writes: list = []
    monkeypatch.setattr(crud, "upsert_git_commits_bulk", lambda *a: writes.append(a))
    await reconcile_issue_fix_flags(async_session, repo.id)
    assert writes == []


@pytest.mark.asyncio
async def test_a_prior_defect_finding_cites_its_linked_issue(async_session):
    repo = await insert_repo(async_session)
    async_session.add(
        FixEvent(
            repository_id=repo.id,
            fix_sha=A,
            file_path="cart.py",
            shape_kind="code_fix",
            old_ranges_json="[]",
            committed_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
    )
    await async_session.flush()
    await store_issue_links(async_session, repo.id, [_link(A, "ENG-1", title="Cart")], source="t")
    rows = [
        {"biomarker_type": "prior_defect", "file_path": "cart.py", "details": {"n": 1}},
        {"biomarker_type": "prior_defect", "file_path": "other.py", "details": {"n": 1}},
        {"biomarker_type": "high_complexity", "file_path": "cart.py", "details": {}},
    ]

    await cite_prior_defect_issues(async_session, repo.id, rows)

    assert rows[0]["details"] == {"n": 1, "linked_issues": ["ENG-1 (bug): Cart"]}
    # No link behind the file, or not a defect finding: left as it was.
    assert rows[1]["details"] == {"n": 1}
    assert rows[2]["details"] == {}


@pytest.mark.asyncio
async def test_a_kept_outcomes_item_is_dropped_so_it_reads_again(async_session):
    import json

    from repowise.core.changes.collect import cached_evidence, forget_kept_outcomes
    from repowise.core.changes.evidence import ChangeEvidence, EvidenceItem
    from repowise.core.persistence.crud.changes import dump_evidence
    from repowise.core.persistence.models import Change

    repo = await insert_repo(async_session)
    kept = ChangeEvidence(
        "pr-1",
        A,
        "2026-01-02T00:00:00+00:00",
        [
            EvidenceItem("health_delta", "Health delta", "ok", "measured"),
            EvidenceItem("outcomes", "Outcomes", "ok", "measured", value={"as_of": A, "days": 30}),
        ],
    )
    row = Change(
        repository_id=repo.id,
        change_id="pr-1",
        identity_kind="pr",
        identity_value="1",
        shape="squash",
        head_sha=A,
        evidence_json=dump_evidence(kept.to_dict()),
    )
    async_session.add(row)
    await async_session.flush()

    assert await forget_kept_outcomes(async_session, repo.id) == 1
    assert [i.kind for i in cached_evidence(row).items] == ["health_delta"]
    assert json.loads(row.evidence_json)["change_id"] == "pr-1"
    assert await forget_kept_outcomes(async_session, repo.id) == 0

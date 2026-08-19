"""Update-time repair of persisted ``author_experience`` + change-risk.

The bug these cover: ``build_commit_rows`` tallies author experience over only
the commits it is handed. An update hands it the commits newer than the last one
persisted, so every author's count restarts at zero and their commits persist as
if a first-timer wrote them — including the ``change_risk_score``, which takes
experience as a feature.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.analysis.change_risk import change_features_from_stored, score_change
from repowise.core.persistence.crud import (
    get_git_commits,
    upsert_git_commits_bulk,
)
from repowise.core.pipeline.incremental import (
    _recompute_commit_experience,
    reconcile_commit_experience,
)
from tests.unit.persistence.helpers import insert_repo


def _stored(sha: str, *, ts: int, email: str = "ann@x", exp: int | None = 0, **over) -> dict:
    """A persisted ``git_commits`` row as ``get_commit_experience_inputs`` returns it."""
    base = {
        "sha": sha,
        "author_name": "Ann",
        "author_email": email,
        "committed_at": datetime.fromtimestamp(ts, tz=UTC),
        "subject": f"commit {sha}",
        "lines_added": 10,
        "lines_deleted": 2,
        "files_changed": 3,
        "dirs_changed": 1,
        "subsystems_changed": 1,
        "entropy": 0.5,
        "is_fix": False,
        "author_experience": exp,
        "change_risk_score": 5.0,
        "change_risk_level": "moderate",
    }
    base.update(over)
    return base


def test_batch_local_experience_is_repaired_across_the_whole_history() -> None:
    """The regression: three updates, each of which restarted the tally at zero."""
    stored = [
        # First index: correct within its own batch.
        _stored("a1", ts=100, exp=0),
        _stored("a2", ts=200, exp=1),
        # Second update batch: restarted at zero.
        _stored("a3", ts=300, exp=0),
        _stored("a4", ts=400, exp=1),
        # Third update batch: restarted again.
        _stored("a5", ts=500, exp=0),
    ]

    updates = {u["sha"]: u["author_experience"] for u in _recompute_commit_experience(stored)}

    assert updates["a3"] == 2
    assert updates["a4"] == 3
    assert updates["a5"] == 4


def test_noreply_identities_fold_into_one_tally() -> None:
    """Both GitHub noreply forms of one login are the same person.

    Squash-merges through the GitHub UI stamp the numeric-id form, so an author
    whose commits arrive by both routes would otherwise split into two tallies
    and stay permanently under the "new contributor" line. Folding to a real
    address is deliberately out of scope (see ``git_indexer.identity``), so
    ``ann@x`` below is a genuinely separate bucket, not an oversight.
    """
    stored = [
        _stored("n1", ts=100, email="123+ann@users.noreply.github.com"),
        _stored("n2", ts=200, email="ann@users.noreply.github.com"),
        _stored("n3", ts=300, email="456+ann@users.noreply.github.com"),
        _stored("plain", ts=400, email="ann@x"),
    ]

    updates = {u["sha"]: u["author_experience"] for u in _recompute_commit_experience(stored)}

    assert updates["n2"] == 1
    assert updates["n3"] == 2
    assert updates["plain"] == 0


def test_distinct_authors_keep_separate_tallies() -> None:
    stored = [
        _stored("a1", ts=100, email="ann@x"),
        _stored("b1", ts=200, email="bob@x"),
        _stored("a2", ts=300, email="ann@x"),
        _stored("b2", ts=400, email="bob@x"),
    ]

    updates = {u["sha"]: u["author_experience"] for u in _recompute_commit_experience(stored)}

    assert updates["a2"] == 1
    assert updates["b2"] == 1


def test_risk_score_is_rescored_to_match_the_corrected_experience() -> None:
    """A corrected experience with a stale score would just hide the error."""
    stored = [_stored("a1", ts=100, exp=0), _stored("a2", ts=200, exp=0)]

    updates = {u["sha"]: u for u in _recompute_commit_experience(stored)}

    row = stored[1]
    expected = score_change(
        change_features_from_stored(
            la=row["lines_added"],
            ld=row["lines_deleted"],
            nf=row["files_changed"],
            nd=row["dirs_changed"],
            ns=row["subsystems_changed"],
            entropy=row["entropy"],
            exp=1,
            is_fix=row["is_fix"],
            author=row["author_name"],
            subject=row["subject"],
            ref=row["sha"],
        )
    )
    assert updates["a2"]["change_risk_score"] == expected.score
    assert updates["a2"]["change_risk_level"] == expected.level


def test_settled_index_produces_no_writes() -> None:
    """A whole-table read must not turn into a whole-table write every update."""
    stored = [_stored("a1", ts=100, exp=0), _stored("a2", ts=200, exp=1)]
    # Give both rows the score their features actually imply.
    for row, exp in ((stored[0], 0), (stored[1], 1)):
        risk = score_change(
            change_features_from_stored(
                la=row["lines_added"],
                ld=row["lines_deleted"],
                nf=row["files_changed"],
                nd=row["dirs_changed"],
                ns=row["subsystems_changed"],
                entropy=row["entropy"],
                exp=exp,
                is_fix=row["is_fix"],
                author=row["author_name"],
                subject=row["subject"],
                ref=row["sha"],
            )
        )
        row["change_risk_score"] = risk.score
        row["change_risk_level"] = risk.level

    assert _recompute_commit_experience(stored) == []


def test_rows_without_a_timestamp_sort_oldest() -> None:
    stored = [_stored("dated", ts=100, exp=9), _stored("undated", ts=0, committed_at=None, exp=9)]

    updates = {u["sha"]: u["author_experience"] for u in _recompute_commit_experience(stored)}

    assert updates["undated"] == 0
    assert updates["dated"] == 1


class _FakeIndexer:
    """Stands in for ``GitIndexer``, whose only role here is reachability."""

    def __init__(self, reachable: set[str] | None) -> None:
        self._reachable = reachable

    def list_reachable_shas(self) -> set[str] | None:
        return self._reachable


@pytest.mark.asyncio
async def test_reconcile_repairs_persisted_rows(async_session) -> None:
    repo = await insert_repo(async_session)
    rows = [
        _stored("a1", ts=100, exp=0),
        _stored("a2", ts=200, exp=0),  # wrong: written by a later update batch
        _stored("a3", ts=300, exp=1),  # wrong for the same reason
    ]
    await upsert_git_commits_bulk(async_session, repo.id, rows)

    await reconcile_commit_experience(async_session, repo.id, _FakeIndexer({"a1", "a2", "a3"}))

    stored = {r.sha: r for r in await get_git_commits(async_session, repo.id, sort="date")}
    assert stored["a1"].author_experience == 0
    assert stored["a2"].author_experience == 1
    assert stored["a3"].author_experience == 2


@pytest.mark.asyncio
async def test_reconcile_prunes_commits_unreachable_from_head(async_session) -> None:
    """Squash-merged branch commits inflate every count until they are dropped."""
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(
        async_session,
        repo.id,
        [_stored("keep1", ts=100), _stored("orphan", ts=200), _stored("keep2", ts=300)],
    )

    await reconcile_commit_experience(async_session, repo.id, _FakeIndexer({"keep1", "keep2"}))

    stored = {r.sha: r for r in await get_git_commits(async_session, repo.id, sort="date")}
    assert set(stored) == {"keep1", "keep2"}
    # The orphan must not have contributed to the surviving tally.
    assert stored["keep2"].author_experience == 1


@pytest.mark.asyncio
async def test_reconcile_never_prunes_when_reachability_is_unknown(async_session) -> None:
    """Shallow clone or failed git: repair experience, but delete nothing."""
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(
        async_session, repo.id, [_stored("a1", ts=100), _stored("a2", ts=200)]
    )

    await reconcile_commit_experience(async_session, repo.id, _FakeIndexer(None))

    stored = {r.sha: r for r in await get_git_commits(async_session, repo.id, sort="date")}
    assert set(stored) == {"a1", "a2"}
    assert stored["a2"].author_experience == 1

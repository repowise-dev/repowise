"""Human-authored release-branch backports count once per change for file ownership."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from repowise.core.ingestion.git_indexer.file_history import (
    _add_ownership,
    _add_span,
    _add_windows,
    _Authors,
)
from repowise.core.ingestion.git_indexer.records import _CommitRec

_NOW = 1_700_000_000
_AUTHOR = ("Sam Bull", "sam@example.com")
_OTHER = ("Koston", "koston@example.com")


def _make_commit(
    sha: str,
    author: tuple[str, str],
    subject: str,
    ts: int,
    added: int = 10,
    deleted: int = 5,
) -> _CommitRec:
    name, email = author
    return _CommitRec(
        sha=sha,
        author_name=name,
        author_email=email,
        ts=ts,
        is_merge=False,
        subject=subject,
        added=added,
        deleted=deleted,
    )


def test_fix_plus_two_human_backports_counts_once_for_author() -> None:
    # A fix plus two backports carrying the original PR number (#13833)
    c1 = _make_commit("1" * 40, _AUTHOR, "Fix cookiejar (#13833)", _NOW - 300)
    c2 = _make_commit(
        "2" * 40,
        _AUTHOR,
        "[PR #13833/b1f6c87b backport][3.14] Fix cookiejar (#13942)",
        _NOW - 200,
    )
    c3 = _make_commit("3" * 40, _AUTHOR, "Fix cookiejar (#13833) (#13836)", _NOW - 100)

    # Two unrelated commits by another author and author themselves
    c4 = _make_commit("4" * 40, _AUTHOR, "Add new feature (#14000)", _NOW - 50)
    c5 = _make_commit("5" * 40, _OTHER, "Refactor session storage (#14001)", _NOW - 10)

    commits = [c1, c2, c3, c4, c5]
    authors = _Authors.tally(commits, _NOW - 1000)

    meta: dict = {
        "commit_count_90d": 0,
        "commit_count_30d": 0,
        "lines_added_90d": 0,
        "lines_deleted_90d": 0,
        "merge_commit_count_90d": 0,
    }
    _add_ownership(meta, authors)

    # The fix + two backports collapsed to 1 change; plus the unrelated commit (#14000) = 2 commits
    top = {a["name"]: a["commit_count"] for a in json.loads(meta["top_authors_json"])}
    assert top["Sam Bull"] == 2
    assert top["Koston"] == 1
    assert meta["contributor_count"] == 2
    # Sam Bull: 2 out of 3 changes
    assert meta["primary_owner_name"] == "Sam Bull"
    assert meta["primary_owner_commit_pct"] == 2 / 3


def test_two_unrelated_commits_still_count_twice() -> None:
    c1 = _make_commit("1" * 40, _AUTHOR, "Fix cookiejar (#13833)", _NOW - 200)
    c2 = _make_commit("2" * 40, _AUTHOR, "Add cookiejar serializer (#13850)", _NOW - 100)

    authors = _Authors.tally([c1, c2], _NOW - 1000)
    meta: dict = {}
    _add_ownership(meta, authors)

    top = {a["name"]: a["commit_count"] for a in json.loads(meta["top_authors_json"])}
    assert top["Sam Bull"] == 2
    assert meta["primary_owner_commit_pct"] == 1.0


def test_churn_and_last_modified_fields_remain_unchanged() -> None:
    # Churn and last-modified measure all commits, not just collapsed changes
    c1 = _make_commit("1" * 40, _AUTHOR, "Fix cookiejar (#13833)", _NOW - 300, added=10, deleted=2)
    c2 = _make_commit(
        "2" * 40,
        _AUTHOR,
        "[PR #13833/b1f6c87b backport][3.14] Fix cookiejar (#13942)",
        _NOW - 100,
        added=10,
        deleted=2,
    )

    commits = [c1, c2]
    now_dt = datetime.fromtimestamp(_NOW, tz=UTC)
    meta: dict = {
        "commit_count_90d": 0,
        "commit_count_30d": 0,
        "lines_added_90d": 0,
        "lines_deleted_90d": 0,
        "merge_commit_count_90d": 0,
    }
    _add_span(meta, commits, commits, now_dt)
    _add_windows(meta, commits, _NOW - 1000, _NOW - 500)
    _add_ownership(meta, _Authors.tally(commits, _NOW - 1000))

    # Author ownership counts 1 change
    top = {a["name"]: a["commit_count"] for a in json.loads(meta["top_authors_json"])}
    assert top["Sam Bull"] == 1

    # Churn adds up all commit lines (10 + 10 = 20 added, 2 + 2 = 4 deleted)
    assert meta["lines_added_90d"] == 20
    assert meta["lines_deleted_90d"] == 4
    # last_commit_at is the newest commit's timestamp (_NOW - 100)
    assert meta["last_commit_at"] == datetime.fromtimestamp(_NOW - 100, tz=UTC)


def test_ordinary_rebase_merge_commits_sharing_pr_count_individually() -> None:
    # Multiple ordinary commits sharing one PR suffix from rebase-merge count individually
    c1 = _make_commit("a" * 40, _AUTHOR, "Add parser (#500)", _NOW - 200)
    c2 = _make_commit("b" * 40, _AUTHOR, "Fix parser tests (#500)", _NOW - 100)

    authors = _Authors.tally([c1, c2], _NOW - 1000)
    meta: dict = {}
    _add_ownership(meta, authors)

    top = {a["name"]: a["commit_count"] for a in json.loads(meta["top_authors_json"])}
    assert top["Sam Bull"] == 2


def test_identical_backport_subjects_without_pr_count_individually() -> None:
    # Identical backport subjects without PR numbers have distinct shas and count individually
    c1 = _make_commit("1" * 40, _AUTHOR, "[backport] Fix merge", _NOW - 200)
    c2 = _make_commit("2" * 40, _AUTHOR, "[backport] Fix merge", _NOW - 100)

    authors = _Authors.tally([c1, c2], _NOW - 1000)
    meta: dict = {}
    _add_ownership(meta, authors)

    top = {a["name"]: a["commit_count"] for a in json.loads(meta["top_authors_json"])}
    assert top["Sam Bull"] == 2


def test_commit_with_earlier_hash_mention_not_merged_into_wrong_pr() -> None:
    # A commit with an issue reference like #3 in the subject does not merge with PR #3
    c1 = _make_commit("1" * 40, _AUTHOR, "Fix #3 backport notes (#120)", _NOW - 200)
    c2 = _make_commit("2" * 40, _AUTHOR, "Fix issue in PR 3 (#3)", _NOW - 100)

    authors = _Authors.tally([c1, c2], _NOW - 1000)
    meta: dict = {}
    _add_ownership(meta, authors)

    top = {a["name"]: a["commit_count"] for a in json.loads(meta["top_authors_json"])}
    assert top["Sam Bull"] == 2


def test_backports_without_original_commit_collapse_to_one() -> None:
    # Backports to multiple release branches without the original commit collapse to 1
    c1 = _make_commit(
        "1" * 40,
        _AUTHOR,
        "[PR #13833/b1f6c87b backport][3.13] Fix cookiejar (#13941)",
        _NOW - 200,
    )
    c2 = _make_commit(
        "2" * 40,
        _AUTHOR,
        "[PR #13833/b1f6c87b backport][3.14] Fix cookiejar (#13942)",
        _NOW - 100,
    )

    authors = _Authors.tally([c1, c2], _NOW - 1000)
    meta: dict = {}
    _add_ownership(meta, authors)

    top = {a["name"]: a["commit_count"] for a in json.loads(meta["top_authors_json"])}
    assert top["Sam Bull"] == 1

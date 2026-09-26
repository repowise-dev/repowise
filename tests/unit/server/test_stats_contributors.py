"""Contributor-count dedup on the stats commit pass.

The "By the Numbers" contributor count keys on commit author identity; GitHub
noreply variants and a person's same-name real+noreply emails must fold to one
person so the headline count isn't inflated.
"""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.stats_highlights import build_commit_pass


def _commit(sha: str, name: str, email: str, ts: int) -> dict:
    return {
        "sha": sha,
        "author_name": name,
        "author_email": email,
        "committed_at": datetime.fromtimestamp(ts, tz=UTC),
        "subject": f"commit {sha}",
        "lines_added": 5,
        "lines_deleted": 1,
        "files_changed": 1,
    }


def test_contributor_count_folds_noreply_and_same_name() -> None:
    rows = [
        # Jane: real email + two noreply variants (numeric id changed), one person.
        _commit("a1", "Jane Doe", "jane@company.com", 1000),
        _commit("a2", "Jane Doe", "12345+jane@users.noreply.github.com", 1100),
        _commit("a3", "Jane Doe", "999+jane@users.noreply.github.com", 1200),
        _commit("b1", "Bob", "bob@company.com", 1300),
    ]
    origin = build_commit_pass(rows)["origin"]
    assert origin["total_commits"] == 4
    assert origin["contributor_count"] == 2


def test_activity_prefers_whole_history_totals() -> None:
    """Index-time totals win over the bounded commit sample (issue #730)."""
    rows = [
        _commit("a1", "Jane Doe", "jane@company.com", 1_600_000_000),
        _commit("a2", "Jane Doe", "jane@company.com", 1_600_086_400),
    ]
    totals = {
        "total_commit_count": 5000,
        "total_contributor_count": 42,
        # Naive, the way SQLite hands it back.
        "first_commit_at": datetime(2011, 3, 13, 7, 6, 40),
        "first_commit_author": "Ada Lovelace",
    }
    out = build_commit_pass(rows, totals)
    origin = out["origin"]
    assert origin["total_commits"] == 5000
    assert origin["contributor_count"] == 42
    assert origin["first_commit_author"] == "Ada Lovelace"
    assert origin["age_days"] > 3000
    # Two commits of 5,000 is a sample, and the payload says so.
    assert out["rhythm"]["window"]["complete"] is False
    assert out["rhythm"]["window"]["commits"] == 2

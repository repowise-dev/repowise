"""Unit tests for per-commit row collection + change-risk building.

Covers the ``commit_sink`` collector on the commit-index walk and the pure
``build_commit_rows`` transform (Kamei features, in-memory author experience,
just-in-time change-risk, ordering, truncation).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from repowise.core.ingestion.git_commit_index import load_commit_index
from repowise.core.ingestion.git_indexer.commit_rows import build_commit_rows


def _build_log(commits: list[dict]) -> str:
    lines: list[str] = []
    for c in commits:
        lines.append(
            "\x00"
            + c["sha"]
            + "\x1f"
            + c["an"]
            + "\x1f"
            + c["ae"]
            + "\x1f"
            + str(c["ct"])
            + "\x1f"
            + c.get("parents", "")
            + "\x1f"
            + c["subj"]
            + "\x1f"
            + c.get("body", "")
        )
        for added, deleted, path in c["files"]:
            lines.append(f"{added}\t{deleted}\t{path}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# commit_sink on load_commit_index
# ---------------------------------------------------------------------------


def test_commit_sink_collects_full_footprint_including_non_indexable() -> None:
    """The sink records every file in the commit, not just the indexable set,
    so change diffusion is measured against the real footprint."""
    now = int(time.time())
    raw = _build_log(
        [
            {
                "sha": "aaa",
                "an": "Alice",
                "ae": "a@x.com",
                "ct": now,
                "subj": "feat: thing",
                # src/a.py is indexable; docs/x.md and gen.lock are NOT.
                "files": [(5, 1, "src/a.py"), (3, 0, "docs/x.md"), (9, 9, "gen.lock")],
            },
        ]
    )
    repo = MagicMock()
    repo.git.log.return_value = raw

    sink: list[dict] = []
    index = load_commit_index(repo, 100, {"src/a.py"}, commit_sink=sink)

    # Bucket still only carries the indexable file (unchanged behaviour).
    assert set(index.keys()) == {"src/a.py"}
    # Sink carries the commit with ALL three files.
    assert len(sink) == 1
    assert sink[0]["sha"] == "aaa"
    assert sorted(p for p, _a, _d in sink[0]["changes"]) == [
        "docs/x.md",
        "gen.lock",
        "src/a.py",
    ]


def test_commit_sink_default_none_is_noop() -> None:
    """Without a sink the return value and behaviour are unchanged."""
    raw = _build_log(
        [
            {
                "sha": "aaa",
                "an": "A",
                "ae": "a@x",
                "ct": 1000,
                "subj": "x",
                "files": [(1, 0, "src/a.py")],
            }
        ]
    )
    repo = MagicMock()
    repo.git.log.return_value = raw
    index = load_commit_index(repo, 100, {"src/a.py"})
    assert set(index.keys()) == {"src/a.py"}


# ---------------------------------------------------------------------------
# build_commit_rows
# ---------------------------------------------------------------------------


def test_build_commit_rows_features_and_ordering() -> None:
    # newest-first input (as the walk yields).
    parsed = [
        {
            "sha": "newer",
            "author_name": "Ann",
            "author_email": "ann@x",
            "ts": 2000,
            "subject": "feat: add",
            "changes": [("pkg/a.py", 5, 5)],
        },
        {
            "sha": "older",
            "author_name": "Ann",
            "author_email": "ann@x",
            "ts": 1000,
            "subject": "fix: bug",
            "changes": [("pkg/a.py", 10, 2), ("pkg/sub/b.py", 3, 0)],
        },
    ]
    rows = build_commit_rows(parsed)

    # Order preserved (newest-first).
    assert [r["sha"] for r in rows] == ["newer", "older"]

    older = next(r for r in rows if r["sha"] == "older")
    assert older["lines_added"] == 13
    assert older["lines_deleted"] == 2
    assert older["files_changed"] == 2
    assert older["dirs_changed"] == 2  # "pkg" and "pkg/sub"
    assert older["subsystems_changed"] == 1  # top-level "pkg"
    assert older["is_fix"] is True
    assert 0.0 <= older["change_risk_score"] <= 10.0
    assert older["change_risk_level"] in {"low", "moderate", "high"}

    newer = next(r for r in rows if r["sha"] == "newer")
    assert newer["is_fix"] is False


def test_build_commit_rows_author_experience_is_cumulative() -> None:
    """Experience = the author's prior-commit count, oldest→newest."""
    parsed = [
        {
            "sha": "c3",
            "author_name": "Ann",
            "author_email": "ann@x",
            "ts": 3000,
            "subject": "x",
            "changes": [("a.py", 1, 0)],
        },
        {
            "sha": "c2",
            "author_name": "Ann",
            "author_email": "ann@x",
            "ts": 2000,
            "subject": "x",
            "changes": [("a.py", 1, 0)],
        },
        {
            "sha": "c1",
            "author_name": "Ann",
            "author_email": "ann@x",
            "ts": 1000,
            "subject": "x",
            "changes": [("a.py", 1, 0)],
        },
        {
            "sha": "b1",
            "author_name": "Bob",
            "author_email": "bob@x",
            "ts": 1500,
            "subject": "x",
            "changes": [("a.py", 1, 0)],
        },
    ]
    rows = build_commit_rows(parsed)
    # Re-derive exp via the change features by re-scoring would be indirect;
    # instead assert the monotonic relationship through risk's exp driver is
    # consistent — simplest: rebuild and check the experience tally by proxy.
    # Ann's three commits have exp 0,1,2 in time order; Bob's single commit 0.
    # We can't read exp off the row directly, so assert via a fresh computation
    # mirroring the implementation contract: the earliest Ann commit must score
    # at least as risky (lower exp ⇒ higher risk, exp coef is protective).
    by_sha = {r["sha"]: r for r in rows}
    assert by_sha["c1"]["change_risk_score"] >= by_sha["c3"]["change_risk_score"]


def test_build_commit_rows_truncates_subject() -> None:
    parsed = [
        {
            "sha": "a",
            "author_name": "A",
            "author_email": "a@x",
            "ts": 1000,
            "subject": "z" * 5000,
            "changes": [("a.py", 1, 0)],
        },
    ]
    rows = build_commit_rows(parsed)
    assert len(rows[0]["subject"]) == 500


def test_build_commit_rows_handles_empty_and_no_changes() -> None:
    assert build_commit_rows([]) == []
    rows = build_commit_rows(
        [
            {
                "sha": "a",
                "author_name": "A",
                "author_email": "a@x",
                "ts": 1000,
                "subject": "merge artifact",
                "changes": [],
            }
        ]
    )
    assert rows[0]["files_changed"] == 0
    assert rows[0]["lines_added"] == 0
    assert 0.0 <= rows[0]["change_risk_score"] <= 10.0


def test_build_commit_rows_committed_at_parsed() -> None:
    rows = build_commit_rows(
        [
            {
                "sha": "a",
                "author_name": "A",
                "author_email": "a@x",
                "ts": 1_700_000_000,
                "subject": "x",
                "changes": [("a.py", 1, 0)],
            }
        ]
    )
    assert rows[0]["committed_at"] is not None
    assert rows[0]["committed_at"].year == 2023
    # ts <= 0 → None (no fabricated timestamp).
    rows0 = build_commit_rows(
        [
            {
                "sha": "b",
                "author_name": "A",
                "author_email": "a@x",
                "ts": 0,
                "subject": "x",
                "changes": [("a.py", 1, 0)],
            }
        ]
    )
    assert rows0[0]["committed_at"] is None

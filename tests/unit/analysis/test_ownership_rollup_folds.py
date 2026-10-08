"""The owner, module and reviewer folds over plain dicts.

A non-SQL producer holds JSON columns already decoded and timestamps as ISO
text; both must fold exactly like the stored text and datetimes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from repowise.core.analysis.health.rows import json_field
from repowise.core.analysis.module_health import aggregate_modules, detail_extras, summarize
from repowise.core.analysis.owners import aggregate_owners, as_utc, silo_modules
from repowise.core.analysis.reviewers import cochange_paths, suggest_reviewers

_ROWS = [
    {
        "file_path": "src/core.py",
        "primary_owner_name": "Alice",
        "primary_owner_email": "alice@example.com",
        "top_authors": [
            {"name": "Alice", "email": "alice@example.com", "commit_count": 8},
            {"name": "Bob", "email": "bob@example.com", "commit_count": 2},
        ],
        "commit_categories": {"fix": 3},
        "co_change_partners": [{"file_path": "lib/util.py", "co_change_count": 4}],
        "commit_count_90d": 6,
        "last_commit_at": datetime(2026, 8, 1, 9, 30),
        "is_hotspot": True,
        "bus_factor": 1,
        "churn_percentile": 0.7,
    },
    {
        "file_path": "lib/util.py",
        "primary_owner_name": "Bob",
        "primary_owner_email": "bob@example.com",
        "top_authors": [{"name": "Bob", "email": "bob@example.com", "commit_count": 5}],
        "commit_categories": {},
        "co_change_partners": [],
        "commit_count_90d": 1,
        "last_commit_at": datetime(2026, 9, 1, tzinfo=UTC),
        "is_hotspot": False,
        "bus_factor": 2,
        "churn_percentile": 0.2,
    },
]

_JSON = {
    "top_authors": "top_authors_json",
    "commit_categories": "commit_categories_json",
    "co_change_partners": "co_change_partners_json",
}


def _stored(row: dict) -> dict:
    return {_JSON.get(k, k): (json.dumps(v) if k in _JSON else v) for k, v in row.items()}


def _artifact(row: dict) -> dict:
    out = {_JSON.get(k, k): v for k, v in row.items()}
    out["last_commit_at"] = row["last_commit_at"].isoformat().replace("+00:00", "Z")
    return out


_DEAD = [{"file_path": "src/core.py", "lines": 4, "primary_owner": "Alice"}]
_SYMBOLS = [{"file_path": "src/core.py", "docstring": "Doc."}]


def test_json_field_reads_text_decoded_and_bad_cells() -> None:
    assert json_field({"x": "[1]"}, "x", []) == [1]
    assert json_field({"x": [1]}, "x", []) == [1]
    assert json_field({"x": ""}, "x", []) == []
    assert json_field({"x": "{bad"}, "x", {}) == {}
    assert json_field({}, "x", {}) == {}


def test_as_utc_reads_naive_aware_and_iso_text() -> None:
    aware = datetime(2026, 8, 1, 9, 30, tzinfo=UTC)
    assert as_utc(datetime(2026, 8, 1, 9, 30)) == aware
    assert as_utc("2026-08-01T09:30:00Z") == aware
    assert as_utc("not a date") is None


def test_owner_fold_is_the_same_over_stored_and_decoded_rows() -> None:
    def fold(rows):
        accs, totals = aggregate_owners(rows, _DEAD)
        return {k: {**vars(a), "file_meta": None} for k, a in accs.items()}, totals

    stored = fold([_stored(r) for r in _ROWS])
    assert stored == fold([_artifact(r) for r in _ROWS])
    accs, totals = stored
    assert totals == {"src": 1, "lib": 1}
    assert accs["alice@example.com"]["dead_code_lines"] == 4
    assert accs["alice@example.com"]["commit_categories"] == {"fix": 2}


def test_module_fold_is_the_same_over_stored_and_decoded_rows() -> None:
    decisions_text = [{"id": "d1", "affected_modules_json": json.dumps(["src"])}]
    decisions_list = [{"id": "d1", "affected_modules_json": ["src"]}]

    def fold(rows, decisions):
        accs = aggregate_modules(rows, _SYMBOLS, _DEAD, decisions)
        return {m: {**summarize(a), **detail_extras(a)} for m, a in accs.items()}

    stored = fold([_stored(r) for r in _ROWS], decisions_text)
    assert stored == fold([_artifact(r) for r in _ROWS], decisions_list)
    assert stored["src"]["governing_decisions"] == ["d1"]
    assert stored["src"]["doc_coverage_pct"] == 100.0


def test_reviewer_fold_is_the_same_over_stored_and_decoded_rows() -> None:
    for shape in (_stored, _artifact):
        rows = [shape(r) for r in _ROWS]
        assert cochange_paths(rows[:1]) == {"lib/util.py"}
        out = suggest_reviewers(rows[:1], rows[1:], limit=5)
        # Alice leads on raw ownership share. Pre-#2862, Bob led instead: the
        # old recency formula spiked on lib/util.py's commits_90d=1 (a
        # file-level artifact, `recent/max(commits_90d, 1)` == 1.0 for any
        # single-commit file), not a genuine per-author recency signal — this
        # fixture's rows carry no ``recent_commit_count`` (pre-reindex data),
        # so the fixed formula gives neither author a recency bonus here.
        assert [s["name"] for s in out] == ["Alice", "Bob"]
        assert out[0]["co_change_paths"] == []


def test_reviewer_recency_ranks_the_recently_active_author_first() -> None:
    """Equal share, different recent activity: the recent author outranks (#2862)."""
    row = {
        "file_path": "src/hot.py",
        "top_authors_json": json.dumps(
            [
                {
                    "name": "Newbie",
                    "email": "newbie@example.com",
                    "commit_count": 5,
                    "recent_commit_count": 5,
                },
                {
                    "name": "OldTimer",
                    "email": "oldtimer@example.com",
                    "commit_count": 5,
                    "recent_commit_count": 0,
                },
            ]
        ),
        "co_change_partners_json": "[]",
        "commit_count_90d": 5,
    }
    out = suggest_reviewers([row], [])
    assert [s["name"] for s in out] == ["Newbie", "OldTimer"]
    assert out[0]["recent_commits"] == 5
    assert out[1]["recent_commits"] == 0


def test_reviewer_recency_is_zero_for_a_row_indexed_before_the_fix() -> None:
    """No ``recent_commit_count`` key: no recency bonus, not an invented estimate (#2862)."""
    row = {
        "file_path": "src/legacy.py",
        "top_authors_json": json.dumps(
            [
                {"name": "Alice", "email": "alice@example.com", "commit_count": 3},
                {"name": "Bob", "email": "bob@example.com", "commit_count": 1},
            ]
        ),
        "co_change_partners_json": "[]",
        "commit_count_90d": 4,
    }
    out = suggest_reviewers([row], [])
    assert [s["recent_commits"] for s in out] == [0, 0]
    assert out[0]["name"] == "Alice"


def test_a_sole_owner_is_a_silo_in_both_rollups() -> None:
    rows = [_stored(r) for r in _ROWS]
    accs, totals = aggregate_owners(rows, [])
    assert silo_modules(accs["alice@example.com"], totals) == 1
    modules = aggregate_modules(rows, [], [], [])
    assert summarize(modules["src"])["is_silo"] is True
    assert summarize(modules["src"])["health_score"] == 34.5

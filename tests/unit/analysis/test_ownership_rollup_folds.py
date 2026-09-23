"""The owner, module and reviewer folds over plain dicts.

A non-SQL producer holds JSON columns already decoded and timestamps as ISO
text; both must fold exactly like the stored text and datetimes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from repowise.core.analysis.health.rows import json_field
from repowise.core.analysis.module_health import aggregate_modules, detail_extras, summarize
from repowise.core.analysis.owners import aggregate_owners, as_utc
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
        assert [s["name"] for s in out] == ["Bob", "Alice"]
        assert out[0]["co_change_paths"] == ["lib/util.py"]

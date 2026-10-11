"""Serving stored dead-code findings: filters, tiers, rollups, serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
)
from repowise.core.analysis.dead_code.serving import (
    DEFAULT_EXCLUDED_KINDS,
    SUMMARY_SCOPE,
    TIER_FLOORS,
    FindingFilters,
    adjust_cross_repo,
    apply_filters,
    build_summary,
    build_tiers,
    compute_impact,
    dead_code_finding_id,
    excluded_kinds,
    last_meaningful_change,
    merge_summary_counts,
    rollup_by_directory,
    rollup_by_owner,
    serialize_finding,
    summary_counts,
)


def _row(path: str, confidence: float, **overrides) -> dict:
    row = {
        "id": f"uuid-{path}-{confidence}",
        "kind": "unused_export",
        "file_path": path,
        "symbol_name": "thing",
        "symbol_kind": "function",
        "start_line": 1,
        "end_line": 10,
        "confidence": confidence,
        "reason": "no references",
        "safe_to_delete": True,
        "lines": 10,
        "last_commit_at": datetime(2026, 1, 2, tzinfo=UTC),
        "commit_count_90d": 0,
        "primary_owner": "Ada",
        "age_days": 400,
    }
    row.update(overrides)
    return row


_ROWS = [
    _row("src/a/one.py", 0.9),
    _row("src/a/two.py", 0.95, lines=3),
    _row("src/b/three.py", 0.5, primary_owner="Grace"),
    _row("lib/four.py", 0.2, primary_owner=None, kind="unreachable_file"),
    _row("src/b/five.py", 0.9, lines=40),
]


def _filters(**overrides) -> FindingFilters:
    base = {
        "kind": None,
        "safe_only": False,
        "min_confidence": 0.0,
        "directory": None,
        "owner": None,
        "excluded_kinds": set(),
    }
    base.update(overrides)
    return FindingFilters(**base)


def test_tier_floors_are_the_engine_thresholds() -> None:
    assert TIER_FLOORS == {
        "high": SAFE_CONFIDENCE_THRESHOLD,
        "medium": RISK_CAP_CONFIDENCE,
        "low": 0.0,
    }


def test_excluded_kinds_follow_the_scope_flags() -> None:
    assert {"unused_internal"} == DEFAULT_EXCLUDED_KINDS
    assert excluded_kinds(
        no_unreachable=True,
        no_unused_exports=True,
        include_internals=True,
        include_zombie_packages=False,
    ) == {"unreachable_file", "unused_export", "zombie_package"}


def test_apply_filters_reads_rows_and_mappings_alike() -> None:
    namespaces = [SimpleNamespace(**r) for r in _ROWS]
    for rows in (_ROWS, namespaces):
        assert len(apply_filters(rows, _filters(min_confidence=0.4))) == 4
        assert len(apply_filters(rows, _filters(kind="unreachable_file"))) == 1
        assert len(apply_filters(rows, _filters(excluded_kinds={"unused_export"}))) == 1
        assert len(apply_filters(rows, _filters(directory="src/a/"))) == 2
        assert len(apply_filters(rows, _filters(owner="grace"))) == 1


def test_applied_echoes_only_what_differs_from_the_defaults() -> None:
    defaults = _filters(min_confidence=RISK_CAP_CONFIDENCE, excluded_kinds=DEFAULT_EXCLUDED_KINDS)
    assert defaults.applied() == {}
    summary: dict = {}
    defaults.summarize(summary)
    assert summary == {"scope": SUMMARY_SCOPE}

    narrowed = _filters(kind="unused_export", safe_only=True, directory="src", owner="Ada")
    assert narrowed.applied() == {
        "kind": "unused_export",
        "safe_only": True,
        "min_confidence": 0.0,
        "directory": "src",
        "owner": "Ada",
    }


def test_build_summary_counts_every_shown_finding() -> None:
    summary = build_summary(summary_counts(_ROWS), 2, _filters(), {})
    assert summary["total_findings"] == 5
    assert summary["filtered_findings"] == 2
    assert summary["by_kind"] == {"unused_export": 4, "unreachable_file": 1}
    assert list(summary)[:5] == [
        "total_findings",
        "filtered_findings",
        "deletable_lines",
        "safe_to_delete_count",
        "by_kind",
    ]
    assert "withheld_types" not in summary


def test_serialization_is_the_same_for_rows_and_mappings() -> None:
    row = _ROWS[0]
    as_dict = serialize_finding(row, repository="r")
    assert serialize_finding(SimpleNamespace(**row), repository="r") == as_dict
    assert as_dict["id"] == dead_code_finding_id(row, "r")
    assert as_dict["last_commit_at"] == "2026-01-02T00:00:00+00:00"


def test_serialization_adds_the_last_meaningful_change() -> None:
    gm = SimpleNamespace(significant_commits_json=json.dumps([{"date": "2026-03-01"}]))
    out = serialize_finding(_ROWS[0], {"src/a/one.py": gm})
    assert out["last_meaningful_change"] == "2026-03-01"
    assert last_meaningful_change(SimpleNamespace(significant_commits_json="not json")) is None
    assert last_meaningful_change(None) is None


def test_build_tiers_bands_sorts_and_reports_overflow() -> None:
    serialized = [serialize_finding(r) for r in _ROWS]
    dropped: list[tuple[str, int]] = []
    tiers = build_tiers(serialized, 2, None, on_overflow=lambda n, b: dropped.append((n, len(b))))

    assert list(tiers) == ["high", "medium", "low"]
    high = tiers["high"]
    assert [f["file_path"] for f in high["findings"]] == ["src/a/two.py", "src/b/five.py"]
    assert (high["count"], high["lines"], high["truncated"]) == (3, 53, True)
    assert dropped == [("high", 1)]
    assert build_tiers(serialized, 2, "low")["low"]["count"] == 1
    assert list(build_tiers(serialized, 2, "medium")) == ["medium"]


def test_single_repo_and_workspace_tiering_have_the_same_shape() -> None:
    # The single-repo path serializes ORM-like rows; the workspace path merges
    # repos, tags each finding with its repo and arrives in a different order.
    single = build_tiers([serialize_finding(SimpleNamespace(**r)) for r in _ROWS], 2, None)
    merged = []
    for r in reversed(_ROWS):
        item = serialize_finding(r)
        item["repo"] = "default"
        merged.append(item)
    workspace = build_tiers(merged, 2, None)

    for tier in workspace.values():
        for f in tier["findings"]:
            f.pop("repo")
    # Equal-score ties keep their input order; this fixture has none.
    assert workspace == single


def test_rollups_group_and_order_by_lines() -> None:
    assert rollup_by_directory(_ROWS)[0] == {
        "directory": "src/b",
        "count": 2,
        "lines": 50,
        "safe_count": 1,
    }
    owners = {o["owner"]: o["count"] for o in rollup_by_owner(_ROWS)}
    assert owners == {"Ada": 3, "Grace": 1, "unowned": 1}


def test_compute_impact_counts_safe_lines_shown() -> None:
    tiers = build_tiers([serialize_finding(r) for r in _ROWS], 25, None)
    impact = compute_impact(tiers)
    assert impact["total_lines_reclaimable"] == 73
    assert impact["safe_lines_reclaimable"] == 53
    assert compute_impact({})["recommendation"] == "No dead code found matching your filters."


class _Lookup:
    def has_cross_repo_consumers(self, repo_alias: str, file_path: str) -> list[dict]:
        return [{"repo": "web"}] if file_path == "src/a/one.py" else []

    def get_repos_depending_on(self, repo_alias: str) -> list[str]:
        return ["mobile"]


def test_adjust_cross_repo_lowers_consumed_findings() -> None:
    tiers = build_tiers([serialize_finding(r) for r in _ROWS], 25, "high")
    adjust_cross_repo(tiers, _Lookup(), "api")
    by_path = {f["file_path"]: f for f in tiers["high"]["findings"]}
    assert by_path["src/a/one.py"]["confidence"] == 0.45
    assert "1 cross-repo consumer(s) in web" in by_path["src/a/one.py"]["cross_repo_note"]
    assert by_path["src/b/five.py"]["confidence"] == 0.27
    assert "mobile" in by_path["src/b/five.py"]["cross_repo_note"]


def test_adjust_cross_repo_without_data_or_alias_is_a_no_op() -> None:
    tiers = build_tiers([serialize_finding(r) for r in _ROWS], 25, None)
    before = json.dumps(tiers, default=str)
    adjust_cross_repo(tiers, None, "api")
    adjust_cross_repo(tiers, _Lookup(), None)
    assert json.dumps(tiers, default=str) == before


def test_merged_per_repo_counts_equal_counting_everything_at_once() -> None:
    parts = [summary_counts(_ROWS[:2]), summary_counts([]), summary_counts(_ROWS[2:])]
    merged = merge_summary_counts(parts)
    whole = summary_counts(_ROWS)
    assert merged == whole
    assert list(merged["by_kind"]) == list(whole["by_kind"])

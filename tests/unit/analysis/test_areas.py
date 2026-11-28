"""Area rollups: directories at any depth folded from stored rows, purely."""

from __future__ import annotations

from datetime import datetime

from repowise.core.analysis.areas import (
    FileOwner,
    ancestors,
    area_detail,
    area_score,
    area_scores,
    attach_deltas,
    baseline_index,
    build_area_index,
    in_area,
    nloc_weights,
    normalize_area,
)
from repowise.core.analysis.areas.paths import area_parent
from repowise.core.analysis.health.aggregation import module_rollups


def _m(path, score, nloc=10, **extra):
    return {"file_path": path, "score": score, "nloc": nloc, "is_test": False, **extra}


METRICS = [
    _m("README.py", 9.0, 5),
    _m("a/x.py", 4.0, 100, line_coverage_pct=50.0, has_test_file=True),
    _m("a/y.py", 9.0, 300, line_coverage_pct=90.0),
    _m("a/b/z.py", 2.0, 50),
    _m("c/w.py", 8.0, 10),
    _m("c/unscored.md", None, 10),
]
GIT = [
    {"file_path": "a/x.py", "is_hotspot": True, "bus_factor": 1},
    {"file_path": "a/y.py", "is_hotspot": False, "bus_factor": 3},
    {"file_path": "a/b/z.py", "is_hotspot": True, "bus_factor": 1},
]
ALICE = FileOwner("alice@example.com", "Alice", "human")
BOT = FileOwner("bot@ci", "ci-bot", "bot")
OWNERS = {"a/x.py": BOT, "a/y.py": BOT, "a/b/z.py": ALICE}
FINDINGS = [
    {"file_path": "a/b/z.py", "severity": "high", "biomarker_type": "complex_method", "id": "1"},
    {"file_path": "a/x.py", "severity": "low", "biomarker_type": "large_method", "id": "2"},
    {"file_path": "c/w.py", "severity": "critical", "biomarker_type": "complex_method", "id": "3"},
]


def _index():
    return build_area_index(METRICS, git_rows=GIT, owners=OWNERS, findings=FINDINGS)


def test_paths_name_every_enclosing_directory():
    assert ancestors("a/b/c.py") == ["", "a", "a/b"]
    assert ancestors("top.py") == [""]
    assert normalize_area("/a\\b/./") == "a/b"
    assert normalize_area(None) == ""
    assert in_area("a/b/c.py", "a") and not in_area("ab/c.py", "a")
    assert in_area("anything.py", "")
    assert area_parent("a/b") == "a" and area_parent("a") == "" and area_parent("") is None


def test_every_directory_rolls_up_with_module_semantics():
    index = _index()
    assert set(index.nodes) == {"", "a", "a/b", "c"}
    # The fold module health uses: same average, same worst performer.
    rows = [{**m, "module": "a"} for m in METRICS if m["file_path"].startswith("a/")]
    (expected,) = module_rollups(rows)
    node = index.nodes["a"]
    assert node["health"] == expected["average_health"]
    assert node["file_count"] == expected["file_count"] == 3
    assert node["worst_file"] == {
        "path": expected["worst_performer_path"],
        "score": expected["worst_performer_score"],
    }
    # Unscored files are in no figure, as in module_rollups.
    assert index.nodes["c"]["file_count"] == 1


def test_tree_shape_counts_direct_files_and_children():
    index = _index()
    assert index.children[""] == ["a", "c"]
    assert index.nodes[""]["direct_file_count"] == 1
    assert index.nodes["a"]["direct_file_count"] == 2
    assert index.nodes["a"]["has_children"] and not index.nodes["a/b"]["has_children"]
    assert index.nodes["a/b"]["depth"] == 2 and index.nodes["a/b"]["parent"] == "a"


def test_signals_bands_coverage_and_findings():
    a = _index().nodes["a"]
    assert a["hotspot_count"] == 2
    assert a["bus_factor_1_count"] == 2
    assert a["band_counts"] == {
        "at_risk": 1, "needs_work": 1, "fair": 0, "good": 0, "excellent": 1
    }
    # NLOC-weighted over measured files only.
    assert a["line_coverage_pct"] == round((50 * 100 + 90 * 300) / 400, 2)
    assert a["coverage_file_count"] == 2
    assert a["findings"] == {"critical": 0, "high": 1, "medium": 0, "low": 1}
    assert _index().nodes["c"]["line_coverage_pct"] is None


def test_automation_never_leads_an_area():
    a = _index().nodes["a"]
    # The bot owns two files, Alice one: Alice still leads, share of all three.
    assert a["primary_owner"]["key"] == "alice@example.com"
    assert a["primary_owner"]["share"] == round(1 / 3, 4)
    assert _index().nodes["c"]["primary_owner"] is None


def test_snapshot_scores_use_current_weights_and_deltas_attach():
    weights = nloc_weights(METRICS)
    old = {"a/x.py": 2.0, "a/y.py": 9.0, "gone/q.py": 5.0}
    new = {"a/x.py": 4.0, "a/y.py": 9.0}
    assert area_score(old, "a", weights) == round((2 * 100 + 9 * 300) / 400, 2)
    assert area_scores(old, weights)["gone"] == 5.0
    assert area_score(new, "c", weights) is None
    index = _index()
    attach_deltas(index.nodes, area_scores(new, weights), area_scores(old, weights))
    assert index.nodes["a"]["health_delta_30d"] == round(
        area_score(new, "a", weights) - area_score(old, "a", weights), 2
    )
    assert index.nodes["c"]["health_delta_30d"] is None


def test_baseline_is_nearest_thirty_days_back_and_never_the_newest():
    days = [datetime(2026, 9, d) for d in (1, 10, 20, 30)]
    assert baseline_index(days) == 0  # Sep 30 - 30d = Aug 31, nearest Sep 1
    assert baseline_index(days[2:]) == 0  # short history: the only older one
    assert baseline_index(days[:1]) is None


def test_detail_lists_are_bounded_and_worst_first():
    index = _index()
    detail = area_detail(index, "a", trend=[{"taken_at": "t", "health": 7.0}], limit=2)
    assert [f["path"] for f in detail["worst_files"]] == ["a/b/z.py", "a/x.py"]
    assert detail["hotspots"]["total"] == 2
    assert detail["owners"]["top"][0]["key"] == "bot@ci"
    assert [f["id"] for f in detail["top_findings"]] == ["1", "2"]
    assert detail["test_reach"]["with_test_file"] == 1
    assert detail["distribution"]["total_files"] == 3
    assert area_detail(index, "nope") is None


def test_area_at_and_choose_depth_split_where_the_work_separates() -> None:
    from repowise.core.analysis.areas import area_at, choose_depth, group_counts

    assert area_at("a/b/c.py", 1) == "a"
    assert area_at("a/b/c.py", 5) == "a/b"
    assert area_at("x.py", 2) == ""
    # One shared chain, then three packages: the split is below the chain.
    weights = {
        "pkg/src/core/a.py": 2,
        "pkg/src/server/b.py": 2,
        "pkg/src/web/c.ts": 1,
    }
    assert choose_depth(weights) == 3
    assert set(group_counts(weights, 3)) == {"pkg/src/core", "pkg/src/server", "pkg/src/web"}
    # Above the small-queue line, three areas are needed before a split counts.
    big = {path: weight * 10 for path, weight in weights.items()}
    two = {"pkg/a/x.py": 10, "pkg/b/y.py": 10}
    assert choose_depth(big) == 3
    assert choose_depth({"pkg/a/x.py": 2, "pkg/b/y.py": 2}) == 2
    assert choose_depth(two) == 2
    # Inside an area, the depth is chosen below it.
    assert choose_depth(weights, base="pkg/src/core") >= 4
    # No depth splits a lone file; the shallowest is kept.
    assert choose_depth({"a/b.py": 3}) == 1
    assert choose_depth({}) == 1
    # Plain paths weigh one each, and a caller can relax the thresholds.
    assert choose_depth(["a/x.py", "b/y.py"], min_areas=1, small=0) == 1
    assert choose_depth(["src/ui/a.ts", "src/web/b.ts", "x/y.py"], base="src", min_areas=1) == 2

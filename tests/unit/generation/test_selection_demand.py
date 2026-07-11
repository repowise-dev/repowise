"""FAQ-weighted per-module file_page reallocation tests.

Exercises the pure allocator (:func:`allocate_module_file_pages`) and the
selector glue (:func:`_select_file_pages`) that maps files to modules. The
contract under test: reallocate the file_page budget toward high-demand
modules while conserving the total, flooring cold modules, staying
deterministic, and reproducing the demand-free selection byte-for-byte when
there is no session data.
"""

from __future__ import annotations

from repowise.core.generation.selection.budget import (
    LEAN_MODULE_FLOOR,
    allocate_module_file_pages,
)
from repowise.core.generation.selection.selector import (
    ModuleGroup,
    _fallback_module,
    _select_file_pages,
)


def _module_of(path: str) -> str:
    return path.split("/")[0]


# Nine candidates, score-descending: module a=3, b=2, c=4 files.
RANKED = ["a/1", "a/2", "a/3", "b/1", "b/2", "c/1", "c/2", "c/3", "c/4"]


# ---------------------------------------------------------------------------
# Passthrough (zero-data byte-identity)
# ---------------------------------------------------------------------------


def test_empty_demand_is_exact_passthrough():
    selected, audit = allocate_module_file_pages(RANKED, 4, {}, _module_of)
    assert selected == RANKED[:4]
    assert audit == []


def test_none_demand_is_exact_passthrough():
    selected, audit = allocate_module_file_pages(RANKED, 4, None, _module_of)
    assert selected == RANKED[:4]
    assert audit == []


def test_demand_not_on_candidates_is_passthrough():
    # Demand names files that are not candidates -> nothing to tilt.
    selected, audit = allocate_module_file_pages(RANKED, 4, {"gone/old.py": 99}, _module_of)
    assert selected == RANKED[:4]
    assert audit == []


# ---------------------------------------------------------------------------
# Conservation
# ---------------------------------------------------------------------------


def test_total_is_conserved():
    selected, _ = allocate_module_file_pages(RANKED, 4, {"c/1": 10}, _module_of)
    assert len(selected) == 4


def test_total_capped_at_candidate_supply():
    selected, _ = allocate_module_file_pages(RANKED, 99, {"c/1": 10}, _module_of)
    assert len(selected) == len(RANKED)
    assert set(selected) == set(RANKED)


def test_zero_budget_returns_empty():
    selected, audit = allocate_module_file_pages(RANKED, 0, {"c/1": 10}, _module_of)
    assert selected == []
    assert audit == []


# ---------------------------------------------------------------------------
# Floor + tilt
# ---------------------------------------------------------------------------


def test_single_module_skew_deepens_hot_and_floors_cold():
    # All demand on module c (baseline gave c zero pages). Budget 4.
    # Baseline: a/1,a/2,a/3,b/1 -> a=3, b=1, c=0.
    demand = {"c/1": 10, "c/2": 10}
    selected, _audit = allocate_module_file_pages(RANKED, 4, demand, _module_of)

    assert len(selected) == 4
    counts = {m: sum(1 for p in selected if _module_of(p) == m) for m in "abc"}
    # Hot module deepened from 0 -> 2; well-covered cold module a leaned to
    # its floor; b kept its floor.
    assert counts["c"] == 2
    assert counts["a"] == LEAN_MODULE_FLOOR
    assert counts["b"] == LEAN_MODULE_FLOOR
    # The floor keeps each cold module's single most important file.
    assert "a/1" in selected and "b/1" in selected
    assert "a/2" not in selected and "a/3" not in selected


def test_floor_never_drops_a_baseline_module_below_lean():
    # Every module has a baseline page; demand piles entirely on one. No
    # module with a baseline page may vanish.
    ranked = ["a/1", "b/1", "c/1", "a/2", "b/2", "c/2"]
    demand = {"a/1": 50, "a/2": 50}
    selected, _ = allocate_module_file_pages(ranked, 3, demand, _module_of)
    modules = {_module_of(p) for p in selected}
    assert {"a", "b", "c"} <= modules  # b and c floored in, not zeroed
    assert len(selected) == 3


def test_hot_module_saturates_then_spills_to_baseline():
    # Demand on module b, but b has only 2 candidates. Extra budget spills
    # back down the demand-free ranking rather than inflating b past supply.
    demand = {"b/1": 100, "b/2": 100}
    selected, _ = allocate_module_file_pages(RANKED, 6, demand, _module_of)
    assert len(selected) == 6
    b_count = sum(1 for p in selected if _module_of(p) == "b")
    assert b_count == 2  # capped at b's candidate supply


# ---------------------------------------------------------------------------
# Determinism + audit
# ---------------------------------------------------------------------------


def test_deterministic_across_runs():
    demand = {"c/1": 10, "c/2": 5, "b/1": 5}
    first, _ = allocate_module_file_pages(RANKED, 5, demand, _module_of)
    second, _ = allocate_module_file_pages(RANKED, 5, demand, _module_of)
    assert first == second


def test_selected_is_ordered_by_original_rank():
    demand = {"c/1": 10, "c/2": 10}
    selected, _ = allocate_module_file_pages(RANKED, 4, demand, _module_of)
    rank = {p: i for i, p in enumerate(RANKED)}
    assert selected == sorted(selected, key=lambda p: rank[p])


def test_audit_reports_demand_and_movement():
    demand = {"c/1": 10, "c/2": 10}
    _, audit = allocate_module_file_pages(RANKED, 4, demand, _module_of)
    rows = {r.module: r for r in audit}
    assert rows["c"].demand == 20
    assert rows["c"].baseline_pages == 0
    assert rows["c"].allocated_pages == 2
    assert rows["c"].delta == 2
    # Audit is demand-descending.
    assert audit[0].module == "c"


# ---------------------------------------------------------------------------
# Selector glue: file -> module mapping
# ---------------------------------------------------------------------------


def _group(key: str, files: tuple[str, ...]) -> tuple[float, ModuleGroup]:
    return (
        1.0,
        ModuleGroup(key=key, display=key, language="python", file_paths=files),
    )


def test_fallback_module_is_top_dir():
    assert _fallback_module("pkg/sub/x.py") == "pkg"
    assert _fallback_module("root_file.py") == "root"


def test_select_file_pages_passthrough_without_demand():
    files = [(9.0, "a/1"), (8.0, "a/2"), (7.0, "b/1")]
    groups = [_group("a", ("a/1", "a/2")), _group("b", ("b/1",))]
    assert _select_file_pages(files, 2, groups, None) == ["a/1", "a/2"]


def test_select_file_pages_tilts_by_module_group():
    # Module groups define the granularity; demand on group b pulls its file in.
    files = [(9.0, "a/1"), (8.0, "a/2"), (7.0, "b/1")]
    groups = [_group("a", ("a/1", "a/2")), _group("b", ("b/1",))]
    demand = {"b/1": 20}
    selected = _select_file_pages(files, 2, groups, demand)
    assert "b/1" in selected  # hot module's file forced in
    assert len(selected) == 2


def test_select_file_pages_uses_fallback_for_ungrouped_file():
    # c/1 is in no module group -> attributed to its top-dir "c".
    files = [(9.0, "a/1"), (8.0, "a/2"), (7.0, "c/1")]
    groups = [_group("a", ("a/1", "a/2"))]
    demand = {"c/1": 20}
    selected = _select_file_pages(files, 2, groups, demand)
    assert "c/1" in selected
    assert len(selected) == 2

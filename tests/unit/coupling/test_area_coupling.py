"""Area-level coupling: placing file pairs in areas, filtering, and folding them."""

from __future__ import annotations

from repowise.core.analysis.areas import area_at, area_depth, choose_depth
from repowise.core.analysis.coupling import (
    MAX_DEPTH,
    CouplingEdge,
    CouplingScope,
    area_coupling,
    area_placer,
    coupled_areas,
    linked_area_pairs,
    place_edges,
)


def _edge(
    a: str,
    b: str,
    strength: float = 1.0,
    *,
    support: int = 3,
    structural: str | None = "unexplained",
    last: str | None = "2026-06-01",
) -> CouplingEdge:
    return CouplingEdge(a, b, strength, last, support=support, structural=structural)


def _no_tests(path: str) -> bool:
    return path.startswith("tests/")


EDGES = [
    _edge("src/ui/a.ts", "src/web/b.ts", 5.0),
    _edge("src/ui/a.ts", "src/ui/c.ts", 4.0, structural="corroborated"),
    _edge("src/core/x.py", "src/web/b.ts", 3.0, structural="corroborated", support=2),
    _edge("src/core/x.py", "tests/test_x.py", 2.0),
    _edge("src/ui/d.ts", "src/web/e.ts", 1.0, last="2025-01-01"),
    _edge("README.md", "src/core/x.py", 0.5, structural="not_applicable"),
]


def test_area_at_partitions_by_level():
    assert area_at("src/ui/a.ts", 1) == "src"
    assert area_at("src/ui/a.ts", 2) == "src/ui"
    # Shallower than the depth: the file's own directory.
    assert area_at("src/ui/a.ts", 5) == "src/ui"
    assert area_at("README.md", 2) == ""


def test_placement_stamps_cross_area_and_keeps_order():
    placed = place_edges(EDGES, CouplingScope(depth=2), is_test=_no_tests)
    assert [p.edge.strength for p in placed] == [5.0, 4.0, 3.0, 2.0, 1.0, 0.5]
    assert placed[0].edge.cross_area is True
    assert (placed[1].source_area, placed[1].edge.cross_area) == ("src/ui", False)


def test_filters_narrow_before_counting():
    def keep(**kw) -> list[tuple[str, str]]:
        placed = place_edges(EDGES, CouplingScope(depth=2, **kw), is_test=_no_tests)
        return [(p.edge.source, p.edge.target) for p in placed]

    assert len(keep(unexplained=True)) == 3
    assert ("src/ui/a.ts", "src/ui/c.ts") not in keep(cross_area=True)
    assert ("src/core/x.py", "tests/test_x.py") not in keep(exclude_tests=True)
    assert ("src/core/x.py", "src/web/b.ts") not in keep(min_support=3)
    # Recency counts back from the newest co-change in the index, not today.
    assert ("src/ui/d.ts", "src/web/e.ts") not in keep(recent_days=90)
    assert keep(area="src/ui", partner="src/web") == [
        ("src/ui/a.ts", "src/web/b.ts"),
        ("src/ui/d.ts", "src/web/e.ts"),
    ]


def test_a_rooted_view_names_outside_files_at_the_roots_level():
    placed = place_edges(EDGES, CouplingScope(depth=1, root="src/ui"), is_test=_no_tests)
    assert {(p.source_area, p.target_area) for p in placed} == {
        ("src/ui", "src/web"),
        ("src/ui", "src/ui"),
    }


def test_area_pairs_rank_unexplained_first_and_carry_their_file_pairs():
    placed = place_edges(EDGES, CouplingScope(depth=2, cross_area=True), is_test=_no_tests)
    out = area_coupling(placed, top_pairs=1)
    first = out["area_pairs"][0]
    assert (first["source"], first["target"]) == ("src/ui", "src/web")
    assert first["pairs"] == 2 and first["unexplained_share"] == 1.0
    assert first["co_changes"] == 6 and first["last_co_change"] == "2026-06-01"
    assert [p["source"] for p in first["top_pairs"]] == ["src/ui/a.ts"]
    totals = out["totals"]
    assert totals["pairs"] == len(placed) == totals["cross_area_pairs"]
    assert totals["unexplained_area_pairs"] == 2 and totals["unlabelled_pairs"] == 0
    web = next(a for a in out["areas"] if a["path"] == "src/web")
    assert web["pairs"] == 3


def test_top_pairs_lead_with_unexplained_ones():
    edges = [_edge("a/1", "b/1", 9.0, structural="corroborated"), _edge("a/2", "b/2", 1.0)]
    placed = place_edges(edges, CouplingScope(depth=1), is_test=_no_tests)
    tops = area_coupling(placed)["area_pairs"][0]["top_pairs"]
    assert [t["source"] for t in tops] == ["a/2", "a/1"]


def test_coupled_areas_names_partners_at_the_areas_depth():
    out = coupled_areas(EDGES, "src/ui", keep=lambda e: not _no_tests(e.target))
    assert [(c["path"], c["pairs"], c["unexplained_pairs"]) for c in out] == [("src/web", 2, 2)]
    assert coupled_areas(EDGES, "") == []


def default_depth(paths, root=""):
    """Coupling's default depth below *root*, as the service chooses it."""
    return choose_depth(paths, base=root, min_areas=1, small=0, max_depth=MAX_DEPTH) - area_depth(root)


def test_default_depth_splits_a_dominant_area():
    paths = ["src/ui/a.ts", "src/web/b.ts", "src/core/x.py", "docs/r.md"]
    assert default_depth(paths) == 2
    assert default_depth(["a/x.py", "b/y.py"]) == 1
    # Nothing deeper to split into.
    assert default_depth(["src/a.py", "src/b.py"]) == 1
    assert default_depth(paths, root="src") == 1


def test_a_dependency_anywhere_between_two_areas_makes_their_pair_expected():
    scope = CouplingScope(depth=2, cross_area=True)
    placed = place_edges(EDGES, scope, is_test=_no_tests)
    # One import from web into ui, between files that never co-change.
    linked = linked_area_pairs([("src/web/z.ts", "src/ui/y.ts")], area_placer(scope))
    assert linked == {("src/ui", "src/web")}
    out = area_coupling(placed, linked=linked)
    verdict = {(p["source"], p["target"]): (p["linked"], p["hidden"]) for p in out["area_pairs"]}
    assert verdict[("src/ui", "src/web")] == (True, False)
    # core and tests: unexplained pairs and no dependency at all.
    assert verdict[("src/core", "tests")] == (False, True)
    # core and web: corroborated pairs only, so nothing to hide.
    assert verdict[("src/core", "src/web")] == (False, False)
    assert out["totals"]["hidden_area_pairs"] == 1
    assert out["area_pairs"][0]["hidden"] is True

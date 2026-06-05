"""Unit tests for the KG validation harness checks (scripts/kg_validate)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "kg_validate"))

from kg_checks import Smell, compute_stats, run_smells  # noqa: E402


def _kg(
    *,
    files: list[tuple[str, str]],  # (path, language)
    imports: list[tuple[str, str]] = (),  # (src_path, dst_path-or-external:..)
    layers: list[dict] | None = None,
    tour: list[dict] | None = None,
    entry_points: list[str] | None = None,
) -> dict:
    nodes = [
        {"id": f"file:{p}", "type": "file", "filePath": p, "language": lang}
        for p, lang in files
    ]
    edges = [
        {
            "source": f"file:{s}",
            "target": f"file:{d}",
            "type": "imports",
            "direction": "forward",
            "weight": 1.0,
        }
        for s, d in imports
    ]
    return {
        "version": "1.0.0",
        "project": {
            "name": "x",
            "total_files": len(files),
            "entry_points": entry_points or [],
        },
        "nodes": nodes,
        "edges": edges,
        "layers": layers or [],
        "tour": tour or [],
    }


SUPPORT = {"go": "full", "python": "full", "elixir": "none"}


class TestComputeStats:
    def test_density_and_resolution(self) -> None:
        kg = _kg(
            files=[("a.go", "go"), ("b.go", "go"), ("c.go", "go")],
            imports=[("a.go", "b.go"), ("a.go", "external:net/http")],
        )
        stats = compute_stats(kg, SUPPORT)
        go = stats["by_language"]["go"]
        assert go["files"] == 3
        assert go["import_edges"] == 2
        assert go["edges_per_file"] == 0.67
        assert go["resolution_rate"] == 0.5
        assert go["import_support"] == "full"

    def test_orphans_count_unconnected_files(self) -> None:
        kg = _kg(
            files=[("a.go", "go"), ("b.go", "go"), ("c.go", "go")],
            imports=[("a.go", "b.go")],
        )
        go = compute_stats(kg, SUPPORT)["by_language"]["go"]
        assert go["orphan_files"] == 1  # c.go has no edges either way
        assert go["orphan_ratio"] == 0.333

    def test_dominant_language(self) -> None:
        kg = _kg(files=[("a.py", "python"), ("b.py", "python"), ("c.go", "go")])
        assert compute_stats(kg, SUPPORT)["dominant_language"] == "python"


class TestSmells:
    def _clean_tour(self) -> list[dict]:
        return [
            {"order": i + 1, "target_path": f"f{i}.py", "kind": "code", "reason": "x"}
            for i in range(7)
        ]

    def test_clean_kg_has_no_smells(self) -> None:
        kg = _kg(
            files=[("f0.py", "python"), ("f1.py", "python")],
            imports=[("f0.py", "f1.py")],
            tour=self._clean_tour(),
        )
        smells = run_smells(kg, compute_stats(kg, SUPPORT))
        assert smells == []

    def test_test_layer_not_last(self) -> None:
        layers = [
            {"id": "layer:test", "name": "Test", "nodeIds": [], "display_order": 0},
            {"id": "layer:app", "name": "Application", "nodeIds": [], "display_order": 1},
        ]
        kg = _kg(files=[("a.py", "python")], layers=layers, tour=self._clean_tour())
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "test_layer_not_last" in codes

    def test_test_file_early_in_tour(self) -> None:
        tour = self._clean_tour()
        tour[1]["target_path"] = "tests/conftest.py"
        kg = _kg(files=[("a.py", "python")], tour=tour)
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "test_file_early_in_tour" in codes

    def test_duplicate_and_example_and_dotdir(self) -> None:
        tour = self._clean_tour()
        tour[2]["target_path"] = "f1.py"  # dup of step 2
        tour[3]["target_path"] = "examples/demo.py"
        tour[4]["target_path"] = ".github/x.py"
        kg = _kg(files=[("a.py", "python")], tour=tour)
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert {"duplicate_tour_targets", "example_file_in_tour", "dot_dir_file_in_tour"} <= codes

    def test_tour_too_short_is_warn(self) -> None:
        kg = _kg(files=[("a.py", "python")], tour=self._clean_tour()[:3])
        smells = run_smells(kg, compute_stats(kg, SUPPORT))
        short = [s for s in smells if s.code == "tour_too_short"]
        assert short and short[0].severity == "WARN"

    def test_edgeless_graph_fails_for_full_support(self) -> None:
        kg = _kg(files=[("a.go", "go"), ("b.go", "go")], tour=self._clean_tour())
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "edgeless_graph" in codes

    def test_edgeless_graph_ok_for_none_support(self) -> None:
        kg = _kg(files=[("a.ex", "elixir"), ("b.ex", "elixir")], tour=self._clean_tour())
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "edgeless_graph" not in codes

    def test_density_regression_against_baseline(self) -> None:
        kg = _kg(
            files=[("a.go", "go"), ("b.go", "go")],
            imports=[("a.go", "b.go")],
            tour=self._clean_tour(),
        )
        stats = compute_stats(kg, SUPPORT)
        baseline = {"stats": {"by_language": {"go": {"edges_per_file": 5.0}}}}
        smells = run_smells(kg, stats, baseline)
        assert any(s.code == "density_regression" and s.severity == "FAIL" for s in smells)

    def test_no_regression_within_tolerance(self) -> None:
        kg = _kg(
            files=[("a.go", "go")],
            imports=[("a.go", "b.go")],
            tour=self._clean_tour(),
        )
        stats = compute_stats(kg, SUPPORT)
        baseline = {"stats": {"by_language": {"go": {"edges_per_file": 1.1}}}}
        assert not any(
            s.code == "density_regression" for s in run_smells(kg, stats, baseline)
        )

    def test_catchall_layer_warns(self) -> None:
        layers = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:f{i}.py" for i in range(9)]},
            {"id": "layer:test", "name": "Test", "display_order": 1,
             "nodeIds": ["file:t.py"]},
        ]
        kg = _kg(
            files=[(f"f{i}.py", "python") for i in range(9)] + [("t.py", "python")],
            imports=[("f0.py", "f1.py")],
            layers=layers,
            tour=self._clean_tour(),
        )
        smells = run_smells(kg, compute_stats(kg, SUPPORT))
        catchall = [s for s in smells if s.code == "catchall_layer"]
        assert catchall and catchall[0].severity == "WARN"

    def test_density_floor_fails_below_tier_floor(self) -> None:
        # 12 go files (full tier, above the small-repo cutoff), one edge:
        # 0.08 e/f clears EDGELESS_FLOOR but not the full-tier floor of 0.9.
        kg = _kg(
            files=[(f"f{i}.go", "go") for i in range(12)],
            imports=[("f0.go", "f1.go")],
            tour=self._clean_tour(),
        )
        smells = run_smells(kg, compute_stats(kg, SUPPORT))
        floor = [s for s in smells if s.code == "density_floor"]
        assert floor and floor[0].severity == "FAIL"

    def test_density_floor_exempts_small_repos(self) -> None:
        # 3 files < SMALL_REPO_FILES: density is noise, floor must not fire.
        kg = _kg(
            files=[("a.go", "go"), ("b.go", "go"), ("c.go", "go")],
            imports=[("a.go", "b.go")],
            tour=self._clean_tour(),
        )
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "density_floor" not in codes

    def test_orphan_ceiling_fails_above_tier_ceiling(self) -> None:
        # 10 files, 4 orphans = 0.4 > the full-tier ceiling of 0.30.
        kg = _kg(
            files=[(f"f{i}.go", "go") for i in range(10)],
            imports=[(f"f{i}.go", f"f{i + 1}.go") for i in range(5)]
            + [(f"f{i}.go", f"f{i}.go") for i in (0,) * 15],  # keep density above floor
            tour=self._clean_tour(),
        )
        stats = compute_stats(kg, SUPPORT)
        assert stats["by_language"]["go"]["orphan_ratio"] > 0.30
        smells = run_smells(kg, stats)
        ceiling = [s for s in smells if s.code == "orphan_ceiling"]
        assert ceiling and ceiling[0].severity == "FAIL"

    def test_catchall_layer_fails_above_95_when_big_enough(self) -> None:
        n = 40
        layers = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:f{i}.py" for i in range(n - 1)]},
            {"id": "layer:test", "name": "Test", "display_order": 1,
             "nodeIds": ["file:t.py"]},
        ]
        kg = _kg(
            files=[(f"f{i}.py", "python") for i in range(n - 1)] + [("t.py", "python")],
            imports=[(f"f{i}.py", f"f{i + 1}.py") for i in range(n - 2)],
            layers=layers,
            tour=self._clean_tour(),
        )
        smells = run_smells(kg, compute_stats(kg, SUPPORT))
        catchall = [s for s in smells if s.code == "catchall_layer"]
        assert catchall and catchall[0].severity == "FAIL"

    def test_catchall_layer_stays_warn_below_size_gate(self) -> None:
        # 96% catch-all but only 10 code files — tiny flat repos are honest.
        layers = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:f{i}.py" for i in range(24)]},
            {"id": "layer:test", "name": "Test", "display_order": 1,
             "nodeIds": ["file:t.py"]},
        ]
        kg = _kg(
            files=[(f"f{i}.py", "python") for i in range(9)] + [("t.py", "python")],
            imports=[(f"f{i}.py", f"f{i + 1}.py") for i in range(8)],
            layers=layers,
            tour=self._clean_tour(),
        )
        smells = run_smells(kg, compute_stats(kg, SUPPORT))
        catchall = [s for s in smells if s.code == "catchall_layer"]
        assert catchall and catchall[0].severity == "WARN"

    def test_entry_point_sanity(self) -> None:
        kg = _kg(
            files=[("a.py", "python")],
            imports=[("a.py", "b.py")],
            tour=self._clean_tour(),
            entry_points=["missing.py", "tests/test_x.py"],
        )
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert {"entry_point_missing", "entry_point_is_test"} <= codes

    def test_entry_claim_without_entry_points(self) -> None:
        tour = self._clean_tour()
        tour[0]["reason"] = "An entry point — execution and imports fan out from here."
        kg = _kg(files=[("f0.py", "python")], imports=[("f0.py", "f1.py")], tour=tour)
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "entry_claim_without_entry_points" in codes

    def test_smell_dataclass_shape(self) -> None:
        s = Smell("WARN", "x", "y")
        assert (s.severity, s.code, s.message) == ("WARN", "x", "y")

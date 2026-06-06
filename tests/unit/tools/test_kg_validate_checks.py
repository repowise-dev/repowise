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
        kg = _kg(files=[(f"f{i}.go", "go") for i in range(10)], tour=self._clean_tour())
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "edgeless_graph" in codes

    def test_edgeless_graph_ok_for_none_support(self) -> None:
        kg = _kg(
            files=[(f"f{i}.ex", "elixir") for i in range(10)], tour=self._clean_tour()
        )
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert "edgeless_graph" not in codes

    def test_tier_gates_exempt_small_repos(self) -> None:
        # 2 files, no imports between them: edgeless/floor/ceiling must all
        # stay quiet — a tiny repo without internal imports is normal.
        kg = _kg(files=[("a.go", "go"), ("b.go", "go")], tour=self._clean_tour())
        codes = {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}
        assert not codes & {"edgeless_graph", "density_floor", "orphan_ceiling"}

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


class TestModuleSmells:
    """Curated wiki module invariants (kg.modules)."""

    def _clean_tour(self) -> list[dict]:
        return [
            {"order": i + 1, "target_path": f"src/app/f{i}.py", "kind": "code", "reason": "x"}
            for i in range(7)
        ]

    def _kg_with_modules(self, n_app: int = 12, n_test: int = 0, modules=None) -> dict:
        app_files = [(f"src/app/f{i}.py", "python") for i in range(n_app)]
        test_files = [(f"tests/t{i}.py", "python") for i in range(n_test)]
        layers = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:{p}" for p, _ in app_files]},
        ]
        if test_files:
            layers.append(
                {"id": "layer:test", "name": "Test", "display_order": 1,
                 "nodeIds": [f"file:{p}" for p, _ in test_files]}
            )
        kg = _kg(
            files=app_files + test_files,
            imports=[(f"src/app/f{i}.py", f"src/app/f{i + 1}.py") for i in range(n_app - 1)],
            layers=layers,
            tour=self._clean_tour(),
        )
        if modules is not None:
            kg["modules"] = modules
        return kg

    def _module(self, name: str, path: str, node_ids: list[str], layer_id: str = "layer:app") -> dict:
        return {
            "id": f"module:{name.replace('/', '-')}",
            "name": name,
            "path": path,
            "layerId": layer_id,
            "nodeIds": node_ids,
        }

    def _codes(self, kg: dict) -> set[str]:
        return {s.code for s in run_smells(kg, compute_stats(kg, SUPPORT))}

    def test_clean_modules_have_no_module_smells(self) -> None:
        app = [(f"src/app/f{i}.py", "python") for i in range(6)]
        web = [(f"src/web/w{i}.py", "python") for i in range(6)]
        layers = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:{p}" for p, _ in app + web]},
        ]
        kg = _kg(files=app + web, imports=[], layers=layers, tour=self._clean_tour())
        kg["modules"] = [
            self._module("app", "src/app", [f"file:{p}" for p, _ in app]),
            self._module("web", "src/web", [f"file:{p}" for p, _ in web]),
        ]
        assert not any(c.startswith("module") for c in self._codes(kg))

    def test_partition_violation_fails(self) -> None:
        kg = self._kg_with_modules()
        ids = [f"file:src/app/f{i}.py" for i in range(12)]
        kg["modules"] = [
            self._module("app", "src/app", ids),
            self._module("dupe", "src/app2", ids[:1]),
        ]
        assert "module_partition" in self._codes(kg)

    def test_coverage_gap_fails(self) -> None:
        kg = self._kg_with_modules()
        kg["modules"] = [
            self._module("app", "src/app", [f"file:src/app/f{i}.py" for i in range(6)])
        ]
        assert "module_coverage" in self._codes(kg)

    def test_name_collision_and_size_suffix_fail(self) -> None:
        kg = self._kg_with_modules()
        ids = [f"file:src/app/f{i}.py" for i in range(12)]
        kg["modules"] = [
            self._module("app (6)", "src/app", ids[:6]),
            {**self._module("app (6)", "src/app2", ids[6:]), "id": "module:app-2"},
        ]
        codes = self._codes(kg)
        assert {"module_name_collision", "module_size_suffix"} <= codes

    def test_generic_only_name_fails(self) -> None:
        # "src" appears in 100% of paths → namespace noise; the module's own
        # path offered an informative segment ("app", 50% of paths), so a
        # generic-only name is a derivation bug, not a forced fallback.
        app = [(f"src/app/f{i}.py", "python") for i in range(6)]
        web = [(f"src/web/w{i}.py", "python") for i in range(6)]
        layers = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:{p}" for p, _ in app + web]},
        ]
        kg = _kg(files=app + web, imports=[], layers=layers, tour=self._clean_tour())
        kg["modules"] = [
            self._module("src", "src/app", [f"file:{p}" for p, _ in app]),
            self._module("web", "src/web", [f"file:{p}" for p, _ in web]),
        ]
        assert "module_generic_name" in self._codes(kg)

    def test_generic_name_exempt_when_no_alternative_exists(self) -> None:
        # Fixture-dominated repos (aeson-shaped): every segment of the module
        # path is dominant — the raw tail is the best name available and
        # flagging it would demand a name that cannot exist.
        files = [(f"suite/case{i}.json", "python") for i in range(10)]
        layers = [
            {"id": "layer:test", "name": "Test", "display_order": 0,
             "nodeIds": [f"file:{p}" for p, _ in files]},
        ]
        kg = _kg(files=files, imports=[], layers=layers, tour=self._clean_tour())
        kg["modules"] = [
            self._module("suite", "suite", [f"file:{p}" for p, _ in files],
                         layer_id="layer:test")
        ]
        assert "module_generic_name" not in self._codes(kg)

    def test_layer_named_module_is_exempt_from_generic_check(self) -> None:
        # Whole-layer fallback names a module after its layer; honest even if
        # the lowercased layer name is also a dominant path segment.
        kg = self._kg_with_modules()
        kg["nodes"] = [
            {"id": f"file:application/f{i}.py", "type": "file",
             "filePath": f"application/f{i}.py", "language": "python"}
            for i in range(12)
        ]
        kg["layers"] = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:application/f{i}.py" for i in range(12)]}
        ]
        kg["modules"] = [
            self._module(
                "Application", "", [f"file:application/f{i}.py" for i in range(12)]
            )
        ]
        codes = self._codes(kg)
        assert "module_generic_name" not in codes

    def test_oversized_with_subdirs_warns_flat_does_not(self) -> None:
        n = 130
        deep_files = [(f"src/app/sub{i % 4}/f{i}.py", "python") for i in range(n)]
        layers = [
            {"id": "layer:app", "name": "Application", "display_order": 0,
             "nodeIds": [f"file:{p}" for p, _ in deep_files]},
        ]
        kg = _kg(files=deep_files, imports=[], layers=layers, tour=self._clean_tour())
        kg["modules"] = [
            self._module("app", "src/app", [f"file:{p}" for p, _ in deep_files])
        ]
        assert "module_oversized" in self._codes(kg)

        flat_files = [(f"src/app/f{i}.py", "python") for i in range(n)]
        layers[0]["nodeIds"] = [f"file:{p}" for p, _ in flat_files]
        kg2 = _kg(files=flat_files, imports=[], layers=layers, tour=self._clean_tour())
        kg2["modules"] = [
            self._module("app", "src/app", [f"file:{p}" for p, _ in flat_files])
        ]
        assert "module_oversized" not in self._codes(kg2)

    def test_confetti_module_count_warns(self) -> None:
        kg = self._kg_with_modules(n_app=32)
        kg["modules"] = [
            self._module(f"m{i}", f"src/app/m{i}", [f"file:src/app/f{i}.py"])
            for i in range(32)
        ]
        assert "module_count" in self._codes(kg)

    def test_module_count_exempts_small_repos(self) -> None:
        # 12 files / 2 modules trips files//8 arithmetically but is honest
        # structure, not confetti — small repos are exempt from the gate.
        kg = self._kg_with_modules(n_app=12)
        kg["modules"] = [
            self._module("a", "src/app/a", [f"file:src/app/f{i}.py" for i in range(6)]),
            self._module("b", "src/app/b", [f"file:src/app/f{i}.py" for i in range(6, 12)]),
        ]
        assert "module_count" not in self._codes(kg)

    def test_test_layer_without_modules_warns(self) -> None:
        kg = self._kg_with_modules(n_app=12, n_test=35)
        kg["modules"] = [
            self._module("app", "src/app", [f"file:src/app/f{i}.py" for i in range(12)])
        ]
        # Test layer (35 files) fully uncovered → coverage FAIL fires too;
        # the dedicated WARN names the user-valued invariant explicitly.
        assert "test_modules_missing" in self._codes(kg)

    def test_modules_missing_warns_on_big_curated_repo(self) -> None:
        kg = self._kg_with_modules(n_app=35)
        assert "modules_missing" in self._codes(kg)

    def test_modules_snapshot_in_stats(self) -> None:
        kg = self._kg_with_modules()
        kg["modules"] = [
            self._module("app", "src/app", [f"file:src/app/f{i}.py" for i in range(12)])
        ]
        snap = compute_stats(kg, SUPPORT)["modules"]
        assert snap == {"count": 1, "names": ["app"], "min_size": 12, "max_size": 12}

"""Tests for curated wiki modules (``derive_modules`` in ``kg_curation``).

Acceptance gates: partition/coverage invariants, determinism, naming
(generic-segment stripping, no size suffixes, collision handling), the
granularity window, stable path-derived ids, and the artifact export seam
(``modules`` key present only when curation derived modules).
"""

from __future__ import annotations

import json

from repowise.core.analysis.kg_curation import derive_modules, validate_kg
from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult

from .kg_fixtures import build_repo, curate

# ---------------------------------------------------------------------------
# Pure derive_modules fixtures
# ---------------------------------------------------------------------------


def _layer(name: str, paths: list[str]) -> dict:
    return {
        "id": f"layer:{name.lower()}",
        "name": name,
        "nodeIds": [f"file:{p}" for p in paths],
    }


def _id_map(*path_lists: list[str]) -> dict[str, str]:
    return {f"file:{p}": p for paths in path_lists for p in paths}


def _monorepo():
    """Synthetic monorepo where ``packages``/``acme``/``src`` are generic.

    175 files total; the namespace dirs appear in 140 (80% > 60% → generic)
    while ``core`` stays at 100/175 (57% → informative).
    """
    ns = "packages/acme/src/acme"
    service = (
        [f"{ns}/core/ingestion/resolvers/r{i}.py" for i in range(30)]
        + [f"{ns}/core/ingestion/parsers/p{i}.py" for i in range(18)]
        + [f"{ns}/core/ingestion/root{i}.py" for i in range(2)]
        + [f"{ns}/core/analysis/a{i}.py" for i in range(30)]
        + [f"{ns}/core/generation/g{i}.py" for i in range(20)]
    )
    ui = [f"{ns}/ui/components/c{i}.tsx" for i in range(40)]
    tests = [f"tests/unit/test_{i}.py" for i in range(20)] + [
        f"tests/integration/test_{i}.py" for i in range(10)
    ]
    config = [f"conf{i}.yaml" for i in range(5)]
    layers = [
        _layer("Service", service),
        _layer("UI", ui),
        _layer("Test", tests),
        _layer("Config", config),
    ]
    return layers, _id_map(service, ui, tests, config)


def _derive(layers, id_to_path, **kw):
    kw.setdefault("target_min", 4)
    kw.setdefault("target_max", 40)
    return derive_modules(layers, id_to_path, **kw)


# ---------------------------------------------------------------------------
# Invariants — partition, coverage, determinism
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_partition_within_each_layer(self):
        layers, id_to_path = _monorepo()
        modules = _derive(layers, id_to_path)
        covered = [nid for m in modules for nid in m["nodeIds"]]
        assert len(covered) == len(set(covered)), "a file appears in two modules"
        # Every layer is ≥ the module floor, so coverage is total.
        all_ids = {nid for layer in layers for nid in layer["nodeIds"]}
        assert set(covered) == all_ids

    def test_never_merges_across_layers(self):
        layers, id_to_path = _monorepo()
        modules = _derive(layers, id_to_path)
        layer_of = {
            nid: layer["id"] for layer in layers for nid in layer["nodeIds"]
        }
        for m in modules:
            assert {layer_of[nid] for nid in m["nodeIds"]} == {m["layerId"]}

    def test_deterministic_byte_equal(self):
        layers, id_to_path = _monorepo()
        a = json.dumps(_derive(layers, id_to_path), sort_keys=True)
        # Re-derive from shuffled inputs: dict/list order must not leak.
        shuffled_layers = [
            {**layer, "nodeIds": list(reversed(layer["nodeIds"]))}
            for layer in layers
        ]
        shuffled_map = dict(reversed(list(id_to_path.items())))
        b = json.dumps(_derive(shuffled_layers, shuffled_map), sort_keys=True)
        assert a == b

    def test_tiny_layer_yields_no_module(self):
        layers, id_to_path = _monorepo()
        tiny = _layer("Data", ["db/schema.sql", "db/seed.sql"])
        id_to_path = {**id_to_path, **_id_map(["db/schema.sql", "db/seed.sql"])}
        modules = _derive([*layers, tiny], id_to_path, min_module_size=3)
        assert not [m for m in modules if m["layerId"] == "layer:data"]

    def test_external_node_ids_ignored(self):
        layers, id_to_path = _monorepo()
        layers[0]["nodeIds"].append("external:@radix-ui/react-progress")
        modules = _derive(layers, id_to_path)
        all_member_ids = {nid for m in modules for nid in m["nodeIds"]}
        assert "external:@radix-ui/react-progress" not in all_member_ids


# ---------------------------------------------------------------------------
# Granularity window
# ---------------------------------------------------------------------------


class TestGranularity:
    def test_window_respected(self):
        layers, id_to_path = _monorepo()
        modules = _derive(layers, id_to_path)
        sizes = {m["name"]: len(m["nodeIds"]) for m in modules}
        # Single-module layers may sit below target_min (rule 3); split
        # products must respect the window.
        assert all(s <= 40 for s in sizes.values()), sizes
        # Mega-layer split landed at the expected granularity.
        assert sizes["ingestion/resolvers"] == 32  # 30 + 2 merged-up root files
        assert sizes["ingestion/parsers"] == 18
        assert sizes["core/analysis"] == 30
        assert sizes["core/generation"] == 20

    def test_flat_dir_stays_one_honest_module(self):
        paths = [f"src/lib/f{i}.py" for i in range(60)]
        modules = _derive([_layer("Service", paths)], _id_map(paths))
        assert len(modules) == 1
        assert len(modules[0]["nodeIds"]) == 60  # > target_max, but flat

    def test_small_remnant_merges_up_not_confetti(self):
        layers, id_to_path = _monorepo()
        modules = _derive(layers, id_to_path)
        # The 2 root files of core/ingestion folded into the biggest
        # ingestion sibling instead of minting a 2-file module.
        split_modules = [m for m in modules if m["layerId"] == "layer:service"]
        assert all(len(m["nodeIds"]) >= 4 for m in split_modules)

    def test_surviving_root_group_named_top_level(self):
        paths = (
            [f"pkg/sub1/f{i}.py" for i in range(10)]
            + [f"pkg/sub2/f{i}.py" for i in range(10)]
            + [f"pkg/r{i}.py" for i in range(6)]
        )
        modules = _derive(
            [_layer("Application", paths)], _id_map(paths), target_max=12
        )
        names = {m["name"] for m in modules}
        assert "Application (top-level)" in names
        assert {"sub1", "sub2"} <= names


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


class TestNaming:
    def test_generic_segments_never_in_names(self):
        layers, id_to_path = _monorepo()
        modules = _derive(layers, id_to_path)
        for m in modules:
            for seg in m["name"].split("/"):
                assert seg not in {"packages", "acme", "src"}, m["name"]

    def test_expected_names(self):
        layers, id_to_path = _monorepo()
        names = {m["name"] for m in _derive(layers, id_to_path)}
        assert names == {
            "ingestion/resolvers",
            "ingestion/parsers",
            "core/analysis",
            "core/generation",
            "UI",
            "Test",
            "Config",
        }

    def test_names_unique_and_never_size_suffixed(self):
        layers, id_to_path = _monorepo()
        names = [m["name"] for m in _derive(layers, id_to_path)]
        assert len(names) == len(set(names))
        assert not any(n.rstrip().endswith(")") and "(" in n and n.split("(")[-1].rstrip(") ").isdigit() for n in names)

    def test_collision_extends_path_leftward(self):
        paths = (
            [f"a/x/utils/f{i}.py" for i in range(8)]
            + [f"b/x/utils/g{i}.py" for i in range(8)]
            + [f"c/api/h{i}.py" for i in range(12)]
        )
        modules = _derive([_layer("Service", paths)], _id_map(paths), target_max=10)
        names = {m["name"] for m in modules}
        # Both dirs end x/utils — disambiguated by the parent segment,
        # NOT by a size suffix.
        assert {"a/x/utils", "b/x/utils"} <= names
        assert not any("(" in n for n in names)

    def test_single_module_layer_named_after_layer(self):
        # Flat-library shape (requests-like): one dir → one module.
        paths = [f"src/requests/m{i}.py" for i in range(12)]
        modules = _derive([_layer("Service", paths)], _id_map(paths))
        assert len(modules) == 1
        assert modules[0]["name"] == "Service"
        assert modules[0]["path"] == "src/requests"

    def test_dominant_language_tag(self):
        layers, id_to_path = _monorepo()
        lang_by_id = {
            nid: ("typescript" if p.endswith(".tsx") else "python")
            for nid, p in id_to_path.items()
        }
        modules = _derive(layers, id_to_path, lang_by_id=lang_by_id)
        by_name = {m["name"]: m for m in modules}
        assert by_name["UI"]["language"] == "typescript"
        assert by_name["core/analysis"]["language"] == "python"


# ---------------------------------------------------------------------------
# Ids — stable, path-derived
# ---------------------------------------------------------------------------


class TestIds:
    def test_ids_are_dir_path_slugs(self):
        layers, id_to_path = _monorepo()
        by_name = {m["name"]: m for m in _derive(layers, id_to_path)}
        m = by_name["ingestion/resolvers"]
        assert m["id"] == "module:packages-acme-src-acme-core-ingestion-resolvers"
        assert m["path"] == "packages/acme/src/acme/core/ingestion/resolvers"

    def test_id_stable_under_file_adds(self):
        layers, id_to_path = _monorepo()
        before = {m["id"] for m in _derive(layers, id_to_path)}
        extra = "packages/acme/src/acme/core/ingestion/resolvers/r_new.py"
        layers2 = [dict(layer) for layer in layers]
        layers2[0] = {
            **layers[0],
            "nodeIds": layers[0]["nodeIds"] + [f"file:{extra}"],
        }
        after = {m["id"] for m in _derive(layers2, {**id_to_path, f"file:{extra}": extra})}
        assert "module:packages-acme-src-acme-core-ingestion-resolvers" in before & after
        assert before == after

    def test_ids_unique(self):
        layers, id_to_path = _monorepo()
        ids = [m["id"] for m in _derive(layers, id_to_path)]
        assert len(ids) == len(set(ids))

    def test_root_path_module_has_empty_path(self):
        layers, id_to_path = _monorepo()
        by_name = {m["name"]: m for m in _derive(layers, id_to_path)}
        assert by_name["Config"]["path"] == ""
        assert by_name["Config"]["id"] == "module:config"


# ---------------------------------------------------------------------------
# Artifact seam — curation populates kg.modules; export key is conditional
# ---------------------------------------------------------------------------


def _curated_repo():
    paths: list[str] = []
    for sub in ("ingestion", "analysis", "generation"):
        paths += [f"packages/core/src/repowise/core/{sub}/mod{i}.py" for i in range(24)]
    paths += [f"packages/cli/src/repowise/cli/commands/cmd{i}.py" for i in range(20)]
    paths += [f"src/api/route{i}.py" for i in range(12)]
    paths += [f"src/utils/util{i}.py" for i in range(8)]
    tests = {f"tests/unit/test_{i}.py" for i in range(30)}
    paths += sorted(tests)
    code = [p for p in paths if p not in tests]
    edges = [
        (code[i], code[i + step]) for step in (1, 2, 3) for i in range(len(code) - step)
    ]
    return build_repo(paths, tests=tests, edges=edges)


class TestArtifactSeam:
    def test_curation_derives_modules(self):
        kg = curate(_curated_repo())
        assert kg.modules, "curated KG should carry derived modules"
        covered = [nid for m in kg.modules for nid in m["nodeIds"]]
        assert len(covered) == len(set(covered))

    def test_flag_off_exports_no_modules_key(self):
        kg = curate(_curated_repo(), enabled=False)
        assert kg.modules == []
        assert "modules" not in kg.to_dict()

    def test_curated_export_carries_modules_key(self):
        kg = curate(_curated_repo())
        data = kg.to_dict()
        assert data["modules"] == kg.to_dict()["modules"]  # deterministic
        assert all(m["nodeIds"] == sorted(m["nodeIds"]) for m in data["modules"])

    def test_from_file_roundtrip(self, tmp_path):
        kg = curate(_curated_repo())
        f = tmp_path / "knowledge-graph.json"
        f.write_text(json.dumps(kg.to_dict()), encoding="utf-8")
        loaded = KnowledgeGraphResult.from_file(f)
        assert loaded is not None
        assert loaded.modules == kg.to_dict()["modules"]

    def test_validate_kg_flags_module_violations(self):
        kg = curate(_curated_repo())
        assert validate_kg(kg).ok
        # Duplicate membership → hard error.
        kg.modules[0]["nodeIds"].append(kg.modules[1]["nodeIds"][0])
        report = validate_kg(kg)
        assert not report.ok
        assert any("more than one module" in e for e in report.errors)

    def test_validate_kg_flags_size_suffix_names(self):
        kg = curate(_curated_repo())
        kg.modules[0]["name"] = "ingestion (32)"
        kg.modules[1]["name"] = "ingestion (18)"
        report = validate_kg(kg)
        assert any("size-suffixed" in e for e in report.errors)

    def test_validate_kg_reports_module_metrics(self):
        kg = curate(_curated_repo())
        metrics = validate_kg(kg).metrics
        assert metrics["module_count"] == len(kg.modules)
        assert metrics["module_coverage_pct"] > 0


class TestMatrixSurfacedShapes:
    """Regression shapes surfaced by the 38-repo validation matrix."""

    def test_fixture_dominated_repo_keeps_real_dir_names(self):
        # aeson shape: one fixture subtree IS >60% of the repo, so every one
        # of its segments is "dominant". Naming must fall back to the raw
        # dir tail — the old "<Layer> (top-level)" fallback collided across
        # sibling groups and tripped the export degradation guard (0 modules).
        parsing = [f"tests/JSONTestSuite/test_parsing/case{i}.json" for i in range(300)]
        transform = [f"tests/JSONTestSuite/test_transform/case{i}.json" for i in range(22)]
        unit = [f"tests/UnitTests/u{i}.hs" for i in range(13)]
        src = [f"src/Data/Aeson/m{i}.hs" for i in range(40)]
        layers = [
            _layer("Application", src),
            _layer("Test", parsing + transform + unit),
        ]
        modules = _derive(layers, _id_map(parsing, transform, unit, src))
        names = [m["name"] for m in modules]
        assert len(names) == len(set(names)), names  # no collision → no degradation
        joined = " ".join(names)
        assert "test_parsing" in joined and "test_transform" in joined
        assert "(top-level)" not in joined

    def test_small_siblings_fuse_into_parent_dir_module(self):
        # django/conf/locale shape: ~30 tiny per-locale dirs. They must fuse
        # into ONE module at the parent dir — not fold into the
        # alphabetically-first locale and misname 170 files as "locale/ar".
        locales = [
            f"django/conf/locale/{loc}/f{i}.py"
            for loc in ("ar", "be", "cs", "da", "de", "el", "es", "fi", "fr", "he")
            for i in range(3)
        ]
        core = [f"django/core/c{i}.py" for i in range(20)]
        layers = [_layer("Application", locales + core)]
        modules = _derive(layers, _id_map(locales, core))
        by_path = {m["path"]: m for m in modules}
        assert "django/conf/locale" in by_path, sorted(by_path)
        assert len(by_path["django/conf/locale"]["nodeIds"]) == 30
        assert by_path["django/conf/locale"]["name"].endswith("locale")

    def test_healthy_module_keeps_identity_absorbing_small_sibling(self):
        # The sibling fuse must not generalise a big module's dir upward:
        # core/providers absorbing a 2-file sibling stays core/providers.
        providers = [f"acme/core/providers/p{i}.py" for i in range(20)]
        stray = [f"acme/core/stray/s{i}.py" for i in range(2)]
        other = [f"acme/web/w{i}.py" for i in range(20)]
        layers = [_layer("Service", providers + stray + other)]
        # target_max=20 forces core(22) to split into providers(20)+stray(2)
        modules = _derive(layers, _id_map(providers, stray, other), target_max=20)
        paths = {m["path"] for m in modules}
        assert "acme/core/providers" in paths, sorted(paths)

    def test_two_all_org_groups_in_one_layer_get_unique_names(self):
        # repowise shape: a root remnant AND a "packages" container group in
        # the same layer both strip to nothing. The container takes its raw
        # tail; only the true root group reads "(top-level)".
        root = ["Makefile.py", "conftest.py", "setup.py", "tasks.py", "noxfile.py"]
        pkgs = [f"packages/p{i}.py" for i in range(6)]
        deep = [f"packages/acme/src/acme/core/d{i}.py" for i in range(40)]
        layers = [_layer("Application", root + pkgs + deep)]
        modules = _derive(layers, _id_map(root, pkgs, deep), target_max=20)
        names = [m["name"] for m in modules]
        assert len(names) == len(set(names)), names

    def test_whole_layer_modules_flagged_for_page_dedupe(self):
        # Single-module layers are 1:1 with their layer page; the flag lets
        # selection skip the duplicate doc while the artifact keeps coverage.
        flat = [f"lib/f{i}.py" for i in range(10)]
        deep_a = [f"acme/web/a{i}.py" for i in range(10)]
        deep_b = [f"acme/api/b{i}.py" for i in range(10)]
        layers = [
            _layer("Service", flat),
            _layer("Application", deep_a + deep_b),
        ]
        modules = _derive(layers, _id_map(flat, deep_a, deep_b), target_max=12)
        by_layer = {}
        for m in modules:
            by_layer.setdefault(m["layerId"], []).append(m)
        assert all(m.get("wholeLayer") for m in by_layer["layer:service"])
        assert len(by_layer["layer:application"]) > 1
        assert not any(m.get("wholeLayer") for m in by_layer["layer:application"])

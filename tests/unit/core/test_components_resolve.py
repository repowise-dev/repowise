"""Derived components, the declared-over-derived merge, and rollup arithmetic."""

from __future__ import annotations

import json

import pytest

from repowise.core.components.catalog import Component
from repowise.core.components.resolve import (
    derive_from_directories,
    derive_from_modules,
    derive_from_services,
    repo_wide_when_flat,
    resolve_components,
)
from repowise.core.components.rollup import rollup_component
from repowise.core.components.store import services_from_system_graph

FILES = [
    "README.md",
    "src/api/routes.py",
    "src/api/v2/routes.py",
    "src/db/models.py",
    "tools/gen.py",
]


def test_directories_are_the_fallback_and_root_files_stay_unassigned():
    resolved = resolve_components([], derive_from_directories(FILES), FILES)

    assert [c.name for c in resolved.components] == ["src", "tools"]
    assert all(c.source == "directory" for c in resolved.components)
    assert resolved.files["tools"] == ["tools/gen.py"]
    assert resolved.unassigned == ["README.md"]


@pytest.mark.parametrize(
    "files",
    [FILES, ["README.md", "packages/a/x.py", "packages/b/y.py", "apps/w/z.py"]],
)
def test_a_repo_with_places_keeps_its_derived_components(files):
    derived = derive_from_directories(files)
    assert repo_wide_when_flat(derived, files, "shop") is derived


@pytest.mark.parametrize(
    "files",
    [
        # Flat: every file at the root (a small Go or Node service).
        [".github/ci.yml", "main.go", "handlers.go", "go.mod"],
        # Layout and test directories only.
        ["README.md", "src/main/java/App.java", "src/main/java/Api.java", "test/AppTest.java"],
        # Most files at the root, beside a directory that names a place.
        ["a.go", "b.go", "c.go", "cmd/svc/main.go"],
        # Code at the root, beside a larger folder of docs that names a place.
        ["a.go", "b.go", "c.go", "d.go", "e.go", *(f"docs/p{i}.md" for i in range(6))],
    ],
)
def test_a_flat_or_layout_only_repo_is_one_repo_wide_component(files):
    derived = repo_wide_when_flat(derive_from_directories(files), files, "Cart Service")
    resolved = resolve_components([], derived, files)

    (comp,) = resolved.components
    assert (comp.name, comp.source) == ("Cart-Service", "repo")
    assert resolved.files[comp.name] == sorted(p for p in files if not p.startswith("."))
    assert resolved.unassigned == sorted(p for p in files if p.startswith("."))


def test_a_module_page_matching_no_file_does_not_divide_the_repo():
    # The structural wiki's "root" module names root-level files, not a directory.
    files = ["service.go", "transport.go", "cmd/svc/main.go", "test/unit.py"]
    derived = repo_wide_when_flat(derive_from_modules([("root", "Root")]), files, "catalogue")
    assert [c.name for c in resolve_components([], derived, files).components] == ["catalogue"]


def test_a_declared_twin_of_the_repo_wide_component_claims_the_repo():
    files = ["main.go", "test/main_test.go"]
    derived = repo_wide_when_flat(derive_from_directories(files), files, "cart")
    declared = [Component(name="cart", owner="group:default/shop")]
    resolved = resolve_components(declared, derived, files)

    assert [(c.name, c.owner) for c in resolved.components] == [("cart", "group:default/shop")]
    assert resolved.files["cart"] == files


def test_module_pages_assign_each_file_to_the_deepest_module():
    derived = derive_from_modules(
        [("src/api", "API"), ("src/api/v2", "API v2"), ("src/db", None), ("src/db", "dup")]
    )
    resolved = resolve_components([], derived, FILES)

    assert {c.name: c.title for c in resolved.components} == {
        "api": "API",
        "db": None,
        "v2": "API v2",
    }
    assert resolved.files == {
        "api": ["src/api/routes.py"],
        "db": ["src/db/models.py"],
        "v2": ["src/api/v2/routes.py"],
    }
    assert resolved.unassigned == ["README.md", "tools/gen.py"]


def _names(roots):
    return {c.root: c.name for c in derive_from_modules((r, None) for r in roots)}


def test_the_shallowest_root_keeps_a_name_and_the_others_take_a_parent():
    assert _names(["a/core", "b/core"]) == {"a/core": "core", "b/core": "core-b"}
    # Layout directories (src, app, ...) do not qualify; the nearest real one does.
    assert _names(["packages/ui/src/workspace", "packages/web/src/app/workspace"]) == {
        "packages/ui/src/workspace": "workspace",
        "packages/web/src/app/workspace": "workspace-web",
    }
    # Losers still tied after one parent both go one parent further out.
    assert _names(["core", "a/x/core", "b/x/core"]) == {
        "core": "core",
        "a/x/core": "core-a",
        "b/x/core": "core-b",
    }


def test_a_declared_component_without_paths_still_annotates_the_derived_one():
    derived = derive_from_modules([("packages/ui", None), ("packages/cli/ui", None)])
    files = ["packages/ui/button.ts", "packages/cli/ui/menu.py"]
    resolved = resolve_components(
        [Component(name="ui", owner="design", criticality="high")], derived, files
    )
    ui = resolved.get("ui")
    assert (ui.root, ui.owner) == ("packages/ui", "design")
    assert resolved.files["ui"] == ["packages/ui/button.ts"]
    assert resolved.files["ui-cli"] == ["packages/cli/ui/menu.py"]


def test_route_parameter_and_group_segments_never_name_a_component():
    assert _names(
        [
            "web/app/repos/[id]",
            "web/app/(marketing)",
            "web/app/docs/[...slug]",
            "web/app/shop/[[...filters]]",
            "api/users/{userId}",
            "(marketing)",
        ]
    ) == {
        "web/app/repos/[id]": "repos",
        "web/app/(marketing)": "app",
        "web/app/docs/[...slug]": "docs",
        "web/app/shop/[[...filters]]": "shop",
        "api/users/{userId}": "users",
        "(marketing)": "marketing",  # a group that is the whole root still names it
    }


def test_template_directories_are_not_route_segments():
    assert _names(["tpl/{{cookiecutter.name}}"]) == {
        "tpl/{{cookiecutter.name}}": "cookiecutter-name"
    }


def test_a_route_under_a_parameter_is_qualified_past_it():
    assert _names(["web/components/settings", "web/app/repos/[id]/settings"]) == {
        "web/components/settings": "settings",
        "web/app/repos/[id]/settings": "settings-repos",
    }


def test_no_component_is_dropped_when_every_readable_name_is_taken():
    # a-b/c and a_b/c lose "c" and slug alike at every level: only the hash tells them apart.
    assert _names(["c", "a-b/c", "a_b/c"]) == {
        "c": "c",
        "a-b/c": "c-1c7275",
        "a_b/c": "c-9eddec",
    }
    # A leaf that slugs to nothing goes straight to the whole path.
    assert _names(["x/__"]) == {"x/__": "x"}


def test_derived_names_depend_only_on_the_set_of_roots():
    import itertools

    roots = ["a/core", "b/x/core", "c/x/core", "web/app/repos/[id]", "lib/util", "d/util"]
    names = _names(roots)
    assert names["b/x/core"] == "core-b" and names["c/x/core"] == "core-c"
    for order in itertools.permutations(roots):
        assert _names(order) == names
    # A deeper same-name root leaves every existing name alone.
    grown = _names([*roots, "e/f/core"])
    assert grown["e/f/core"] == "core-f"
    assert {r: grown[r] for r in roots} == names


def test_services_come_from_the_repo_s_own_system_graph_nodes():
    graph = {
        "nodes": [
            {"repo": "shop", "service_path": "services/cart", "name": "cart"},
            {"repo": "shop", "service_path": None, "name": "shop"},
            {"repo": "other", "service_path": "svc", "name": "svc"},
        ]
    }
    services = services_from_system_graph(graph, "shop")
    assert services == [("cart", "services/cart")]
    (comp,) = derive_from_services(services)
    assert (comp.name, comp.source, comp.root) == ("cart", "service", "services/cart")
    assert services_from_system_graph(None, "shop") == []


def test_declared_paths_claim_first_and_derived_keep_the_rest():
    declared = [
        Component(name="routing", paths=("**/routes.py",)),
        Component(name="db", owner="data", criticality="high"),  # no paths: inherits
    ]
    derived = derive_from_modules([("src/api", None), ("src/db", None)])
    resolved = resolve_components(declared, derived, FILES)

    names = [c.name for c in resolved.components]
    # Declared first in catalog order; "api" lost every file, so it is dropped.
    assert names == ["routing", "db"]
    assert resolved.files["routing"] == ["src/api/routes.py", "src/api/v2/routes.py"]
    db = resolved.get("DB")
    assert db is not None and db.declared and db.root == "src/db" and db.owner == "data"
    assert resolved.files["db"] == ["src/db/models.py"]


def test_first_declared_wins_an_overlap():
    declared = [
        Component(name="one", paths=("src/**",)),
        Component(name="two", paths=("src/db/**",)),
    ]
    resolved = resolve_components(declared, [], FILES)
    assert resolved.files["two"] == []
    assert "src/db/models.py" in resolved.files["one"]


def test_rollup_folds_existing_per_file_facts():
    files = ["a.py", "b.py", "c.py", "test_a.py"]
    git_rows = {
        "a.py": {
            "file_path": "a.py",
            "is_hotspot": True,
            "temporal_hotspot_score": 0.9,
            "bus_factor": 1,
            "primary_owner_name": "Ann",
            "primary_owner_email": "ann@x.io",
            "top_authors_json": json.dumps([{"name": "Ann", "email": "ann@x.io", "count": 3}]),
        },
        "b.py": {
            "file_path": "b.py",
            "is_hotspot": True,
            "temporal_hotspot_score": 0.5,
            "bus_factor": 2,
            "primary_owner_name": "Ann",
            "primary_owner_email": "ann@x.io",
            "top_authors_json": "[]",
        },
        "c.py": {
            "file_path": "c.py",
            "is_hotspot": False,
            "temporal_hotspot_score": 0.1,
            "bus_factor": 1,
            "primary_owner_name": "Ben",
            "primary_owner_email": "ben@x.io",
            "top_authors_json": "[]",
        },
    }
    health_rows = {
        "a.py": {"score": 4.0, "nloc": 300, "has_test_file": True, "line_coverage_pct": 50.0},
        "b.py": {"score": 10.0, "nloc": 100, "has_test_file": False, "line_coverage_pct": None},
        "c.py": {"score": None, "nloc": 100, "has_test_file": False, "line_coverage_pct": None},
        "test_a.py": {"score": 9.0, "nloc": 50, "has_test_file": True, "is_test": True},
    }
    dead_rows = [("b.py", 20), ("b.py", 5), ("elsewhere.py", 99)]

    facts = rollup_component(
        files,
        test_files={"test_a.py"},
        git_rows=git_rows,
        health_rows=health_rows,
        dead_rows=dead_rows,
        dead_code_ran=True,
    )

    assert facts["file_count"] == 4
    health = facts["health"]
    # NLOC-weighted over scored files: (4*300 + 10*100 + 9*50) / 450.
    assert health["average"] == pytest.approx(round((1200 + 1000 + 450) / 450, 2))
    assert health["scored_files"] == 3
    assert (health["worst_path"], health["basis"]) == ("a.py", "measured")
    assert facts["hotspots"]["count"] == 2
    assert facts["hotspots"]["top"] == ["a.py", "b.py"]
    # Ann owns 2 of 3 files, over half: one owner covers it.
    assert facts["bus_factor"]["value"] == 1
    assert facts["bus_factor"]["single_owner_files"] == 2
    assert (facts["bus_factor"]["owned_files"], facts["bus_factor"]["coverage"]) == (3, 0.75)
    # Three owned files are too few to measure a truck factor.
    assert facts["bus_factor"]["basis"] == "inferred" and "too few" in facts["bus_factor"]["note"]
    reach = facts["test_reach"]
    assert (reach["reached_files"], reach["production_files"]) == (1, 3)
    assert reach["share"] == 0.33 and reach["basis"] == "inferred"
    assert reach["line_coverage"] == {
        "pct": 50.0,
        "measured_files": 1,
        "basis": "measured",
        "method": reach["line_coverage"]["method"],
    }
    dead = facts["dead_code"]
    assert (dead["findings"], dead["files_with_findings"], dead["lines"]) == (2, 1, 25)
    assert dead["share_of_nloc"] == 0.05  # 25 / 500 production NLOC
    assert all("basis" in figure for figure in facts.values() if isinstance(figure, dict))
    assert "Ann" not in json.dumps(facts) and "ann@" not in json.dumps(facts)


def test_rollup_says_unavailable_rather_than_zero():
    facts = rollup_component(
        ["a.py"], test_files=set(), git_rows={}, health_rows={}, dead_rows=[],
        dead_code_ran=False,
    )
    for key in ("health", "hotspots", "bus_factor", "test_reach", "dead_code"):
        assert facts[key]["basis"] == "unavailable", key


def _owned(path: str, owner: str) -> dict:
    return {
        "file_path": path,
        "is_hotspot": False,
        "bus_factor": 1,
        "primary_owner_name": owner,
        "primary_owner_email": f"{owner.lower()}@x.io",
        "top_authors_json": "[]",
    }


def test_bus_factor_is_measured_from_five_owned_files():
    files = [f"f{i}.py" for i in range(6)]
    git_rows = {p: _owned(p, "Ann" if i < 4 else "Ben") for i, p in enumerate(files)}
    facts = rollup_component(
        files, test_files=set(), git_rows=git_rows, health_rows={}, dead_rows=[],
        dead_code_ran=True,
    )
    bus = facts["bus_factor"]
    assert (bus["value"], bus["owned_files"], bus["coverage"], bus["basis"]) == (1, 6, 1.0, "measured")
    assert "note" not in bus


def test_dead_code_that_never_ran_is_unavailable_not_zero():
    facts = rollup_component(
        ["a.py"], test_files=set(), git_rows={}, health_rows={"a.py": {"score": 8.0, "nloc": 10}},
        dead_rows=[], dead_code_ran=False,
    )
    assert facts["dead_code"] == {
        "basis": "unavailable",
        "reason": "dead-code analysis has not run for this repository",
    }
    ran = rollup_component(
        ["a.py"], test_files=set(), git_rows={}, health_rows={"a.py": {"score": 8.0, "nloc": 10}},
        dead_rows=[], dead_code_ran=True,
    )
    assert (ran["dead_code"]["findings"], ran["dead_code"]["basis"]) == (0, "inferred")


def test_gated_languages_are_left_out_of_health_and_dead_code(monkeypatch):
    # No shipped row is gated: run on the audit's gated table.
    from repowise.core.analysis import finding_registry
    from tests.unit.server.mcp.test_language_gates import AUDIT_GATED

    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", AUDIT_GATED)
    health_rows = {
        "A.java": {"score": 2.0, "nloc": 100},
        "b.py": {"score": 8.0, "nloc": 100},
    }
    facts = rollup_component(
        ["A.java", "b.py"], test_files=set(), git_rows={}, health_rows=health_rows,
        dead_rows=[("A.java", 50), ("b.py", 10)], dead_code_ran=True,
    )
    health, dead = facts["health"], facts["dead_code"]
    assert (health["average"], health["scored_files"], health["gated_files"]) == (8.0, 1, 1)
    assert health["method"].endswith("; 1 file(s) in gated languages excluded")
    assert (dead["findings"], dead["lines"], dead["gated_files"]) == (1, 10, 1)
    assert dead["share_of_nloc"] == 0.1
    assert dead["method"].endswith("; 1 file(s) in gated languages excluded")


def test_only_gated_files_make_health_and_dead_code_unavailable(monkeypatch):
    # No shipped row is gated: run on the audit's gated table.
    from repowise.core.analysis import finding_registry
    from tests.unit.server.mcp.test_language_gates import AUDIT_GATED

    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", AUDIT_GATED)
    facts = rollup_component(
        ["A.java", "B.cs"], test_files=set(), git_rows={},
        health_rows={"A.java": {"score": 2.0, "nloc": 10}, "B.cs": {"score": 3.0, "nloc": 10}},
        dead_rows=[("A.java", 5)], dead_code_ran=True,
    )
    assert facts["health"]["basis"] == "unavailable" and facts["health"]["gated_files"] == 2
    assert facts["dead_code"]["basis"] == "unavailable" and facts["dead_code"]["gated_files"] == 2

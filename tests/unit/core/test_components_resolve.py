"""Derived components, the declared-over-derived merge, and rollup arithmetic."""

from __future__ import annotations

import json

import pytest

from repowise.core.components.catalog import Component
from repowise.core.components.resolve import (
    derive_from_directories,
    derive_from_modules,
    derive_from_services,
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


def test_colliding_names_fall_back_to_the_full_path():
    derived = derive_from_modules([("a/core", None), ("b/core", None)])
    assert [c.name for c in derived] == ["core", "b-core"]


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
        ["a.py"], test_files=set(), git_rows={}, health_rows={}, dead_rows=[]
    )
    for key in ("health", "hotspots", "bus_factor", "test_reach"):
        assert facts[key]["basis"] == "unavailable", key

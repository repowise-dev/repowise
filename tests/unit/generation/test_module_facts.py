"""The facts a module page is written from, and the diagram its shape calls for."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from repowise.core.generation.context.contexts import ModulePageContext
from repowise.core.generation.context.module_facts import (
    build_module_facts,
    git_activity,
    importing_tests,
    module_base,
    module_budget,
    symbol_weight,
)

_FILES = [
    "src/shop/orders/place.py",
    "src/shop/orders/models.py",
    "src/shop/billing/charge.py",
    "src/shop/stock/reserve.py",
    "src/shop/__init__.py",
]


@dataclass
class _Flow:
    trace: list[str]
    entry_point_score: float = 1.0
    reach: int = 0


def _ctx(files=_FILES, entry_points=()) -> ModulePageContext:
    return ModulePageContext(
        title="Shop",
        language="python",
        total_symbols=0,
        public_symbols=0,
        entry_points=list(entry_points),
        dependencies=[],
        dependents=[],
        pagerank_mean=0.0,
        files=list(files),
    )


def _graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for f in [*_FILES, "src/web/views.py", "tests/test_shop.py", "src/db/session.py"]:
        g.add_node(f, is_test=f.startswith("tests/"))
    g.nodes["tests/test_shop.py"]["is_test"] = True

    def imports(a, b, *names):
        g.add_edge(a, b, edge_type="imports", imported_names=list(names))

    imports("src/shop/orders/place.py", "src/shop/billing/charge.py", "charge")
    imports("src/shop/orders/place.py", "src/shop/stock/reserve.py", "reserve")
    imports("src/shop/billing/charge.py", "src/shop/orders/models.py", "Order")
    imports("src/shop/__init__.py", "src/shop/orders/place.py", "place_order")
    imports("src/shop/stock/reserve.py", "src/db/session.py", "session")
    imports("src/web/views.py", "src/shop/orders/place.py", "place_order")
    imports("tests/test_shop.py", "src/shop/orders/place.py", "place_order")
    for sym in ("place_order", "charge", "reserve", "save"):
        g.add_node(f"x::{sym}", node_type="symbol", name=sym)
    return g


def test_the_base_is_the_deepest_shared_directory():
    assert module_base(_FILES) == "src/shop"
    assert module_base(["a.py", "b/c.py"]) == ""


def test_parts_and_the_imports_between_them():
    facts = build_module_facts(_ctx(), [], _graph())

    assert {p["part"] for p in facts["parts"]} == {"orders/", "billing/", "stock/", "__init__.py"}
    edges = {(e["from"], e["to"]): e for e in facts["imports_between_parts"]}
    assert edges[("orders/", "billing/")]["names"] == ["charge"]
    assert ("billing/", "orders/") in edges
    # A package entry file re-exports everything; its edges would make a star.
    assert not any(a == "__init__.py" for a, _ in edges)


def test_neighbours_are_named_by_directory_and_tests_do_not_count():
    facts = build_module_facts(_ctx(), [], _graph())

    assert facts["depends_on"] == [{"area": "src/db/", "imports": 1, "names": ["session"]}]
    assert facts["used_by"] == [{"area": "src/web/", "imports": 1, "names": ["place_order"]}]


def test_parts_with_pages_of_their_own_are_the_parts_drawn():
    ctx = _ctx()
    ctx.child_pages = [{"title": "Ordering", "path": "src/shop/orders"}]
    facts = build_module_facts(ctx, [], _graph())

    edges = {(e["from"], e["to"]) for e in facts["imports_between_parts"]}
    assert ("Ordering", "billing/") in edges
    assert facts["child_pages"] == ctx.child_pages


def test_tests_that_import_the_module_are_listed_with_their_names():
    parsed = {"tests/test_shop.py": SimpleNamespace(symbols=[
        SimpleNamespace(name="test_places_an_order"), SimpleNamespace(name="helper"),
    ])}
    assert importing_tests(_graph(), _FILES, parsed) == [
        {"file": "tests/test_shop.py", "tests": ["test_places_an_order"]}
    ]
    assert importing_tests(_graph(), ["src/shop/stock/reserve.py"], parsed) == []


def test_change_history_names_where_changes_and_fixes_cluster():
    meta = {
        "src/shop/orders/place.py": {"file_path": "src/shop/orders/place.py",
                                     "commit_count_90d": 5, "prior_defect_count": 2},
        "src/shop/stock/reserve.py": {"file_path": "src/shop/stock/reserve.py",
                                      "commit_count_90d": 1, "prior_defect_count": 0},
    }
    assert git_activity(_FILES, meta, "src/shop") == {
        "commits_90d": 6,
        "most_changed_90d": [
            {"file": "orders/place.py", "commit_count_90d": 5},
            {"file": "stock/reserve.py", "commit_count_90d": 1},
        ],
        "most_bug_fixes": [{"file": "orders/place.py", "prior_defect_count": 2}],
    }
    assert git_activity(_FILES, None, "src/shop") == {}


def test_symbols_other_code_runs_through_weigh_more():
    g = _graph()
    g.add_node("src/shop/billing/charge.py::charge", node_type="symbol",
               file_path="src/shop/billing/charge.py")
    g.add_edge("src/shop/orders/place.py::place_order", "src/shop/billing/charge.py::charge",
               edge_type="calls")
    flow = _Flow(["src/shop/orders/place.py::place_order", "src/shop/billing/charge.py::charge"])
    weight = symbol_weight(g, _FILES, [flow])
    assert weight["src/shop/billing/charge.py::charge"] == 3
    assert weight["src/shop/orders/place.py::place_order"] == 2


def test_the_default_style_reads_and_writes_more_for_a_bigger_module():
    small, big = module_budget(4, 4, True), module_budget(400, 30, True)
    assert small.words == (550, 750) and big.words == (900, 1100)
    assert small.excerpt_tokens < big.excerpt_tokens == 15_000
    # A map page owning no files of its own reads little code.
    assert module_budget(400, 0, True).excerpt_tokens == 4_000
    # Other styles keep one size for every module.
    assert module_budget(4, 4, False) == module_budget(400, 30, False)


def test_connected_parts_get_a_flowchart():
    assert build_module_facts(_ctx(), [], _graph())["diagram"] == "flowchart"


def test_a_hub_with_a_flow_that_starts_here_gets_a_sequence():
    flow = _Flow(
        [
            "src/shop/orders/place.py::place_order",
            "src/shop/billing/charge.py::charge",
            "src/shop/stock/reserve.py::reserve",
            "src/db/session.py::save",
        ]
    )
    facts = build_module_facts(
        _ctx(entry_points=["src/shop/orders/place.py"]), [], _graph(), [flow]
    )

    assert facts["diagram"] == "sequenceDiagram"
    assert [s["part"] for s in facts["traced_flow"]] == [
        "orders/",
        "billing/",
        "stock/",
        "outside: src/db/",
    ]
    assert facts["traced_flow"][0]["file"] == "orders/place.py"


def test_the_flow_drawn_is_the_one_that_drives_the_most_code():
    """Not the one crossing the most parts: that picked a niche command once."""
    niche = _Flow(
        [
            "src/shop/orders/place.py::reprint",
            "src/shop/billing/charge.py::fmt",
            "src/shop/stock/reserve.py::fmt",
            "src/shop/__init__.py::fmt",
        ],
        reach=5,
    )
    main = _Flow(
        [
            "src/shop/orders/place.py::place_order",
            "src/shop/orders/place.py::validate",
            "src/shop/billing/charge.py::charge",
            "src/db/session.py::save",
        ],
        reach=80,
    )
    facts = build_module_facts(
        _ctx(entry_points=["src/shop/orders/place.py"]), [], _graph(), [niche, main]
    )

    assert facts["traced_flow"][0]["call"] == "place_order"


def test_a_flow_that_only_passes_through_is_not_used():
    flow = _Flow(
        [
            "src/web/views.py::render",
            "src/shop/orders/place.py::place_order",
            "src/shop/billing/charge.py::charge",
            "src/shop/stock/reserve.py::reserve",
        ]
    )
    facts = build_module_facts(_ctx(), [], _graph(), [flow])

    assert "traced_flow" not in facts


def test_a_small_unconnected_module_draws_nothing():
    files = ["lib/one.py", "lib/two.py"]
    facts = build_module_facts(_ctx(files=files), [], nx.DiGraph())

    assert facts["diagram"] == "none"
    assert "imports_between_parts" not in facts


def test_the_facts_are_identical_under_any_hash_seed():
    """The facts are part of the prompt, and a page is reused only when the
    prompt is byte-identical, so set iteration order must not leak into them."""
    script = (
        "import json, sys; sys.path.insert(0, sys.argv[1]);"
        "import test_module_facts as t;"
        "from repowise.core.generation.context.module_facts import build_module_facts;"
        "print(json.dumps(build_module_facts(t._ctx(), [], t._graph())))"
    )
    here = str(Path(__file__).parent)
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script, here],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2", "3")
    }
    assert len(outputs) == 1

"""The facts a module page is written from, and the diagram its shape calls for."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from repowise.core.generation.context.contexts import ModulePageContext
from repowise.core.generation.context.module_facts import build_module_facts, module_base

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

    assert facts["depends_on"] == [{"area": "src/db/", "imports": 1}]
    assert facts["used_by"] == [{"area": "src/web/", "imports": 1}]


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

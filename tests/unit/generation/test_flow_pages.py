"""Flow pages: which operations get a walkthrough, and which steps it shows."""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.cascade import build_page_dependencies, expand_cascade
from repowise.core.generation.flow_pages import (
    flow_page_limit,
    flow_pages_of_files,
    select_flow_plans,
)
from repowise.core.generation.page_generator.flow import (
    embed_flow_diagram,
    flow_context,
    flow_diagram,
)


def _graph(calls: dict[str, list[str]], *, tests: tuple[str, ...] = ()) -> nx.DiGraph:
    """Symbol ``path::name`` nodes and call edges, one line apart in each caller."""
    g = nx.DiGraph()
    nodes = set(calls) | {c for cs in calls.values() for c in cs}
    for line, node in enumerate(sorted(nodes), start=1):
        path, name = node.split("::", 1)
        g.add_node(path, node_type="file", is_test=path.startswith("tests/"))
        g.add_node(
            node,
            node_type="symbol",
            kind="function",
            name=name,
            file_path=path,
            start_line=line * 10,
            end_line=line * 10 + 5,
            visibility="public",
        )
    for caller, callees in calls.items():
        for n, callee in enumerate(callees):
            g.add_edge(caller, callee, edge_type="calls", confidence=1.0, call_lines=[100 + n])
    for test in tests:
        g.add_edge(test, next(iter(calls)), edge_type="calls", confidence=1.0)
    return g


# One operation that runs through three areas, and the helpers it shares.
_CALLS = {
    "cli/run.py::run_command": ["cli/run.py::parse_args", "engine/index.py::build_index"],
    "engine/index.py::build_index": [
        "engine/parse.py::parse_files",
        "store/write.py::save",
        "util/log.py::log",
    ],
    "engine/parse.py::parse_files": ["engine/parse.py::parse_one", "util/log.py::log"],
    "store/write.py::save": ["store/db.py::connect", "store/db.py::commit", "util/log.py::log"],
    "store/db.py::connect": ["store/db.py::open_pool", "store/db.py::migrate"],
    "engine/parse.py::parse_one": ["engine/parse.py::tokenize", "engine/parse.py::resolve"],
    "cli/run.py::parse_args": ["util/log.py::log", "cli/run.py::defaults"],
}


def test_the_operation_nobody_calls_is_the_flow_and_its_steps_are_real_calls():
    g = _graph(_CALLS)
    (plan,) = select_flow_plans(g)
    assert plan.entry_id == "cli/run.py::run_command"
    assert plan.title == "How run_command works"
    names = [s.name for s in plan.steps]
    assert names[0] == "run_command"
    # Every step after the first is called by an earlier one.
    for i, step in enumerate(plan.steps[1:], start=1):
        assert 0 <= step.parent < i
        assert g.has_edge(plan.steps[step.parent].symbol_id, step.symbol_id)
    assert {"build_index", "parse_files", "save"} <= set(names)
    assert all(s.start_line and s.end_line for s in plan.steps)


def test_a_test_calling_the_entry_does_not_stop_it_being_one():
    g = _graph(_CALLS, tests=("tests/test_run.py::test_run",))
    g.add_node("tests/test_run.py::test_run", node_type="symbol", kind="function", name="test_run",
               file_path="tests/test_run.py")
    assert [p.entry_id for p in select_flow_plans(g)] == ["cli/run.py::run_command"]


def test_a_helper_reaching_little_is_not_an_operation():
    g = _graph({"a.py::tiny": ["a.py::x", "a.py::y"]})
    assert select_flow_plans(g) == []


def test_selection_is_deterministic():
    g = _graph(_CALLS)
    assert select_flow_plans(g) == select_flow_plans(_graph(_CALLS))


def test_the_page_budget_grows_with_the_repo():
    assert [flow_page_limit(n) for n in (10, 500, 2000, 9000)] == [4, 6, 8, 10]


def test_an_edit_to_any_step_file_marks_the_flow_page_stale():
    (plan,) = select_flow_plans(_graph(_CALLS))
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], flow_pages_of=flow_pages_of_files([plan])
    )
    result = expand_cascade({"file_page:store/write.py"}, "none", deps)
    assert plan.page_id in result.stale_ids


def test_the_diagram_boxes_are_the_steps_and_link_to_their_module_page():
    g = _graph(_CALLS)
    (plan,) = select_flow_plans(g)
    modules = {"engine/index.py": ("module_page:engine", "Engine")}
    diagram = flow_diagram(plan, modules)
    assert diagram.startswith("```mermaid\nflowchart TD")
    assert diagram.count('["<b>') == len(plan.steps)
    assert 'click S' in diagram and "module_page%3Aengine" in diagram
    ctx = flow_context(plan, g, modules, {})
    assert ctx["steps"][0]["cite"] == f"cli/run.py:{plan.steps[0].start_line}-{plan.steps[0].end_line}"
    assert ctx["modules"] == ["Engine"]


def test_the_diagram_goes_before_the_first_section():
    out = embed_flow_diagram("# T\n\nOpening.\n\n## Phase\n\nText.\n", "```mermaid\nx\n```\n")
    assert out.index("```mermaid") < out.index("## Phase")


def test_a_reused_page_keeps_one_diagram():
    g = _graph(_CALLS)
    (plan,) = select_flow_plans(g)
    diagram = flow_diagram(plan, {})
    once = embed_flow_diagram("# T\n\nOpening.\n\n## Phase\n\nText.\n", diagram)
    assert embed_flow_diagram(once, diagram).count("```mermaid") == 1


def test_a_graph_that_cannot_be_walked_warns_and_leaves_no_dependencies():
    from structlog.testing import capture_logs

    from repowise.core.generation.scope import _flow_dependencies

    class Broken:
        def __init__(self) -> None:
            self._parsed_files = {"a.py": None}

        def graph(self):
            raise ValueError("graph not built")

    with capture_logs() as logs:
        assert _flow_dependencies([], Broken()) == {}
    assert [(e["log_level"], e["event"]) for e in logs] == [
        ("warning", "cascade.flow_dependencies_failed")
    ]


def test_flow_dependencies_map_each_step_file_to_its_page():
    from types import SimpleNamespace

    from repowise.core.generation.scope import _flow_dependencies

    g = _graph(_CALLS)
    builder = SimpleNamespace(graph=lambda: g, _parsed_files={})
    deps = _flow_dependencies([], builder)
    assert deps["store/write.py"] == ("flow_page:cli/run.py::run_command",)

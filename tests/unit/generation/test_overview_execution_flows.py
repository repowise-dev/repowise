"""The overview's execution-flow section, end to end.

``assemble_repo_overview`` reads the traced flows off the graph builder and the
overview templates render them. Every step of that is asserted here because the
section has never appeared on a generated page: the assembler read attribute
names the ``ExecutionFlow`` dataclass does not have, the ``AttributeError`` was
swallowed by a bare ``except``, and the ``{% if ctx.execution_flows %}`` guard
then dropped the section without a word.
"""

from __future__ import annotations

from dataclasses import replace

import networkx as nx
import pytest
from structlog.testing import capture_logs

from repowise.core.analysis.execution_flows import ExecutionFlow, ExecutionFlowReport
from repowise.core.generation.context_assembler import ContextAssembler


class _Builder:
    """Minimal stand-in for the graph builder's three overview-facing calls."""

    def __init__(self, flows, graph=None):
        self._flows = flows
        # An empty graph resolves no entry point to a file, which is the
        # "cannot tell" case: the reachability filter keeps every flow.
        self._graph = graph if graph is not None else nx.DiGraph()

    def community_info(self):
        return []

    def graph(self):
        return self._graph

    def execution_flows(self):
        return ExecutionFlowReport(
            total_entry_points_scored=len(self._flows),
            total_flows=len(self._flows),
            flows=list(self._flows),
        )


def _flow(entry_point_id: str, score: float, trace: list[str]) -> ExecutionFlow:
    return ExecutionFlow(
        entry_point_id=entry_point_id,
        entry_point_name=entry_point_id.rsplit("::", 1)[-1],
        entry_point_score=score,
        trace=trace,
        depth=len(trace) - 1,
        crosses_community=False,
        communities_visited=[0],
    )


@pytest.fixture
def flows():
    return [
        _flow("packages/cli/src/repowise/cli/main.py::main", 0.9123, ["a", "b", "c"]),
        _flow("packages/server/src/repowise/server/app.py::create_app", 0.4567, ["d", "e"]),
    ]


def test_overview_context_carries_traced_flows(sample_config, sample_repo_structure, flows):
    """The reason the section never rendered: this list was always empty."""
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows)
    )

    assert len(ctx.execution_flows) == 2
    first = ctx.execution_flows[0]
    assert first["entry_point"] == "packages/cli/src/repowise/cli/main.py::main"
    assert first["entry_point_name"] == "main"
    assert first["score"] == 0.912
    assert first["trace_length"] == 3


def test_overview_context_ranks_flows_by_score(sample_config, sample_repo_structure):
    """The template renders the first five, so the order is the selection."""
    assembler = ContextAssembler(sample_config)
    unordered = [
        _flow("low.py::f", 0.1, ["a", "b"]),
        _flow("high.py::f", 0.9, ["a", "b"]),
        _flow("mid.py::f", 0.5, ["a", "b"]),
    ]
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(unordered)
    )
    assert [f["entry_point"] for f in ctx.execution_flows] == [
        "high.py::f",
        "mid.py::f",
        "low.py::f",
    ]


# ---------------------------------------------------------------------------
# a flow entry point nothing can reach is dead code, not a front door
# ---------------------------------------------------------------------------


def _graph_with(files: dict[str, dict], imports: list[tuple[str, str]]) -> nx.DiGraph:
    """A file graph plus one symbol node per file, as the real builder shapes it."""
    g = nx.DiGraph()
    for path, attrs in files.items():
        g.add_node(path, node_type="file", language="python", **attrs)
        g.add_node(f"{path}::f", node_type="symbol", file_path=path, name="f")
        g.add_edge(path, f"{path}::f", edge_type="defines")
    for src, dst in imports:
        g.add_edge(src, dst, edge_type="imports")
    return g


def test_unreachable_entry_point_is_not_a_flow(sample_config, sample_repo_structure):
    """Zero inbound calls is the strongest positive signal for a flow entry
    point and the definition of ``unreachable_file``. The same file was
    Primary Execution Flow #1 and dead code on the same index."""
    graph = _graph_with(
        {
            "src/orphan.py": {"is_entry_point": False},
            "src/service.py": {"is_entry_point": False},
            "src/caller.py": {"is_entry_point": False},
        },
        imports=[("src/caller.py", "src/service.py")],
    )
    flows = [_flow("src/orphan.py::f", 0.95, ["a", "b"]), _flow("src/service.py::f", 0.4, ["a"])]

    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows, graph)
    )

    assert [f["entry_point"] for f in ctx.execution_flows] == ["src/service.py::f"]


def test_conventional_entry_point_survives_having_no_importers(
    sample_config, sample_repo_structure
):
    """Nothing imports ``main.py`` either, which is why the dead-code pass
    exempts it, and why this filter must too."""
    graph = _graph_with({"src/main.py": {"is_entry_point": True}}, imports=[])
    flows = [_flow("src/main.py::f", 0.95, ["a", "b"])]

    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows, graph)
    )

    assert [f["entry_point"] for f in ctx.execution_flows] == ["src/main.py::f"]


@pytest.mark.parametrize(
    "path",
    [
        "internal/scheduler/queue.go",  # Go imports name a package
        "src/main/java/app/Queue.java",
        "src/Queue.kt",
        "src/engine/queue.cpp",
        "include/engine/queue.h",
        "src/features/api/index.ts",  # barrel: reached by the names it forwards
        "pkg/sub/__init__.py",
    ],
)
def test_filter_bails_out_where_dead_code_is_kinder(sample_config, sample_repo_structure, path):
    """The analyzer rescues these from unreachable_file, so the flow stays.

    The barrels are rescued by the shared predicate outright. The
    package-granular languages are rescued because this caller passes no
    package map, which the predicate reads as "not checked" and answers
    reachable — the forgiving direction, chosen at the call site rather than
    falling out of what state happened to be available. Over-dropping empties
    the section silently, which is the failure being avoided.
    """
    graph = _graph_with({path: {"is_entry_point": False}}, imports=[])
    flows = [_flow(f"{path}::f", 0.95, ["a", "b"])]

    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows, graph)
    )

    assert [f["entry_point"] for f in ctx.execution_flows] == [f"{path}::f"]


@pytest.mark.parametrize("attr", ["is_api_contract", "is_never_flag"])
def test_flow_survives_the_rescues_the_analyzer_already_applied(
    sample_config, sample_repo_structure, attr
):
    """Both flags live on the graph node, so this caller always had them and
    never asked. Sharing the predicate means it does now, and the two passes
    stop disagreeing about a published API contract with no importer."""
    graph = _graph_with({"src/contract.py": {"is_entry_point": False, attr: True}}, imports=[])
    flows = [_flow("src/contract.py::f", 0.95, ["a", "b"])]

    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows, graph)
    )

    assert [f["entry_point"] for f in ctx.execution_flows] == ["src/contract.py::f"]


def test_self_import_does_not_rescue_a_flow_entry_point(sample_config, sample_repo_structure):
    """A file importing itself is not evidence anything else reaches it."""
    graph = _graph_with({"src/orphan.py": {"is_entry_point": False}}, imports=[])
    graph.add_edge("src/orphan.py", "src/orphan.py", edge_type="imports")
    flows = [_flow("src/orphan.py::f", 0.95, ["a", "b"])]

    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows, graph)
    )

    assert ctx.execution_flows == []


def test_dropping_a_flow_is_logged(sample_config, sample_repo_structure):
    """An empty section is a fine outcome; a silent one is not."""
    graph = _graph_with({"src/orphan.py": {"is_entry_point": False}}, imports=[])
    flows = [_flow("src/orphan.py::f", 0.95, ["a", "b"])]

    assembler = ContextAssembler(sample_config)
    with capture_logs() as logs:
        assembler.assemble_repo_overview(
            sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows, graph)
        )

    dropped = [e for e in logs if e["event"] == "overview_flows_dropped_unreachable"]
    assert len(dropped) == 1
    assert dropped[0]["dropped"] == 1
    assert dropped[0]["kept"] == 0


def test_co_change_alone_does_not_make_an_entry_point_reachable(
    sample_config, sample_repo_structure
):
    """Co-change is a historical association, not a caller."""
    graph = _graph_with({"src/orphan.py": {"is_entry_point": False}}, imports=[])
    graph.add_node("docs/notes.md", node_type="file", language="markdown")
    graph.add_edge("docs/notes.md", "src/orphan.py", edge_type="co_changes")
    flows = [_flow("src/orphan.py::f", 0.95, ["a", "b"])]

    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows, graph)
    )

    assert ctx.execution_flows == []


# ---------------------------------------------------------------------------
# barrels are not execution entry points
# ---------------------------------------------------------------------------


def test_barrels_rank_below_real_entry_points(sample_config, sample_repo_structure):
    """``is_entry_point`` is a filename heuristic whose generic stem set
    includes ``index``, so a buried re-export barrel arrived indistinguishable
    from a front door and could lead the list.

    Demoted rather than dropped: ``packages/cli/src/index.ts`` is a genuine
    package front door in a monorepo, and dropping every glue stem would lose
    it.
    """
    # ``sample_repo_structure`` is module-scoped, so mutating it in place leaks
    # into every later test in this file.
    structure = replace(
        sample_repo_structure,
        entry_points=[
            "src/features/checkout/api/index.ts",  # buried re-export leaf
            "packages/cli/src/index.ts",  # buried, but a real package entry
            "src/main.py",  # conventional name, shallow
        ],
    )

    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {})

    # Conventional name first; nothing lost.
    assert ctx.entry_points[0] == "src/main.py"
    assert set(ctx.entry_points) == set(structure.entry_points)


def test_overview_context_warns_when_flows_cannot_be_read(sample_config, sample_repo_structure):
    """A builder that raises must say so. This is the failure that hid for a year."""

    class _Broken:
        def community_info(self):
            return []

        def execution_flows(self):
            raise RuntimeError("graph not built")

    assembler = ContextAssembler(sample_config)
    with capture_logs() as logs:
        ctx = assembler.assemble_repo_overview(
            sample_repo_structure, {}, [], {}, graph_builder=_Broken()
        )

    assert ctx.execution_flows == []
    warned = [e for e in logs if e["event"] == "overview_execution_flows_unavailable"]
    assert len(warned) == 1
    assert warned[0]["error_type"] == "RuntimeError"
    assert warned[0]["log_level"] == "warning"


def test_overview_context_no_builder_is_silent(sample_config, sample_repo_structure):
    """No builder is not a failure, so it must not warn."""
    assembler = ContextAssembler(sample_config)
    with capture_logs() as logs:
        ctx = assembler.assemble_repo_overview(sample_repo_structure, {}, [], {})
    assert ctx.execution_flows == []
    assert [e for e in logs if e["event"].startswith("overview_")] == []


# ---------------------------------------------------------------------------
# rendered output — both templates read the same context key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", ["repo_overview.j2", "stub/repo_overview.j2"])
def test_rendered_overview_names_the_entry_point(
    sample_config, sample_repo_structure, flows, template
):
    """Both templates were always correct — they read the context dict, whose
    keys never changed. They rendered nothing because the list reaching them
    was empty. Asserted on both so a future key rename fails here rather than
    quietly emptying the section again."""
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(MockProvider(), assembler, sample_config)
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, {}, [], {}, graph_builder=_Builder(flows)
    )
    out = gen._render(template, ctx=ctx, repo_git_summary=None)

    assert "## Primary Execution Flows" in out
    assert "packages/cli/src/repowise/cli/main.py::main" in out
    assert "``" not in out.split("## Primary Execution Flows", 1)[1].split("##", 1)[0]


# ---------------------------------------------------------------------------
# the same field names, read by the other consumer
# ---------------------------------------------------------------------------


def test_how_it_works_flow_traces_carry_entry_point_and_score(flows):
    """``_collect_flows`` reads the same dataclass and had the same defect.

    Its two ``getattr`` calls carried defaults, so instead of raising it
    produced a ``FlowTrace`` with an empty entry point and a zero score for
    every flow on every page ever generated.
    """
    from types import SimpleNamespace

    from repowise.core.generation.onboarding.subkinds.how_it_works import _collect_flows

    long_enough = [
        _flow("packages/cli/src/repowise/cli/main.py::main", 0.9, ["a", "b", "c", "d"]),
    ]
    signals = SimpleNamespace(
        graph_builder=SimpleNamespace(
            execution_flows=lambda: ExecutionFlowReport(
                total_entry_points_scored=1, total_flows=1, flows=long_enough
            )
        )
    )

    traces = _collect_flows(signals)

    assert len(traces) == 1
    assert traces[0].entry_point == "packages/cli/src/repowise/cli/main.py::main"
    assert traces[0].score == pytest.approx(0.9)

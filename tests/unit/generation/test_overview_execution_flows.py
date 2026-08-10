"""The overview's execution-flow section, end to end.

``assemble_repo_overview`` reads the traced flows off the graph builder and the
overview templates render them. Every step of that is asserted here because the
section has never appeared on a generated page: the assembler read attribute
names the ``ExecutionFlow`` dataclass does not have, the ``AttributeError`` was
swallowed by a bare ``except``, and the ``{% if ctx.execution_flows %}`` guard
then dropped the section without a word.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from repowise.core.analysis.execution_flows import ExecutionFlow, ExecutionFlowReport
from repowise.core.generation.context_assembler import ContextAssembler


class _Builder:
    """Minimal stand-in for the graph builder's two overview-facing calls."""

    def __init__(self, flows):
        self._flows = flows

    def community_info(self):
        return []

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

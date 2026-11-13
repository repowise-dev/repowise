"""``run_generation`` honours ``test_run`` by limiting the file set.

Regression anchor for #1505: ``--test-run`` was forwarded end-to-end but never
consumed, because the orchestrator's truncation branch was gated on
``test_run and generate_docs`` and init always runs the pipeline with
``generate_docs=False``, delegating generation to a later phase. The top-10
PageRank cap belongs in the generation entry itself, so it works regardless of
how the pipeline got there.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.pipeline.phases.generation import run_generation


class _StubGenerator:
    """Records the parsed_files it was asked to generate, then returns pages."""

    def __init__(self) -> None:
        self.parsed_files: list | None = None

    async def generate_all(self, parsed_files, *args, **kwargs) -> list:
        self.parsed_files = list(parsed_files)
        return []


@pytest.fixture(autouse=True)
def _stub_generator(monkeypatch: pytest.MonkeyPatch) -> _StubGenerator:
    stub = _StubGenerator()
    monkeypatch.setattr(
        "repowise.core.generation.PageGenerator",
        lambda *a, **k: stub,
    )
    return stub


def _parsed_files(n: int) -> list:
    files = []
    for i in range(n):
        files.append(SimpleNamespace(file_info=SimpleNamespace(path=f"pkg/mod_{i}.py")))
    return files


def _graph_builder_for(paths: list[str]):
    """A bare GraphBuilder whose graph has a node per file (pagerank on a
    graph with no edges is uniform, so ranking never drops any file before
    the cap)."""
    from repowise.core.ingestion.graph.builder import GraphBuilder

    b = GraphBuilder()
    for p in paths:
        b.graph().add_node(p, node_type="file")
    return b


async def test_test_run_limits_generation_to_top_10_files(
    _stub_generator: _StubGenerator, tmp_path: pytest.TempPathFactory
) -> None:
    paths = [f"pkg/mod_{i}.py" for i in range(15)]
    await run_generation(
        repo_path=tmp_path,
        parsed_files=_parsed_files(15),
        source_map={},
        graph_builder=_graph_builder_for(paths),
        repo_structure=SimpleNamespace(),
        git_meta_map={},
        llm_client=SimpleNamespace(),
        embedder=None,
        vector_store=None,
        concurrency=1,
        progress=None,
        test_run=True,
    )
    assert _stub_generator.parsed_files is not None
    assert len(_stub_generator.parsed_files) == 10


async def test_no_test_run_generates_every_file(
    _stub_generator: _StubGenerator, tmp_path: pytest.TempPathFactory
) -> None:
    paths = [f"pkg/mod_{i}.py" for i in range(15)]
    await run_generation(
        repo_path=tmp_path,
        parsed_files=_parsed_files(15),
        source_map={},
        graph_builder=_graph_builder_for(paths),
        repo_structure=SimpleNamespace(),
        git_meta_map={},
        llm_client=SimpleNamespace(),
        embedder=None,
        vector_store=None,
        concurrency=1,
        progress=None,
    )
    assert _stub_generator.parsed_files is not None
    assert len(_stub_generator.parsed_files) == 15


def test_test_run_rank_ignores_co_changes() -> None:
    """Only a code edge lifts a file into the cap; co-change history does not (#5472)."""
    from repowise.core.pipeline.phases._common import limit_to_top_pagerank

    paths = [f"pkg/mod_{i}.py" for i in range(5)]
    builder = _graph_builder_for(paths)
    g = builder.graph()
    g.add_edge("pkg/mod_1.py", "pkg/mod_4.py", edge_type="imports")
    for src in ("pkg/mod_0.py", "pkg/mod_2.py"):
        g.add_edge(src, "pkg/mod_3.py", edge_type="co_changes")

    top = limit_to_top_pagerank(_parsed_files(5), builder, n=1)

    assert [pf.file_info.path for pf in top] == ["pkg/mod_4.py"]

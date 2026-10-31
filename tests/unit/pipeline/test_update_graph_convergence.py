"""The update path must build the same graph as the init path.

The incremental rebuild used to skip two edge passes the init pipeline runs:
dynamic hints (never executed on update) and the repo-wide ``co_changes``
edges (re-added only for the changed files, on a graph rebuilt from scratch).
The update-built graph therefore structurally diverged from the init-built
one: metrics persisted by an update disagreed with init's for identical code,
and the first post-init update could never hit the centrality cache.

These tests build both graph shapes over the same small git repo and assert
node/edge convergence plus equality of the centrality-cache signatures (the
exact keys the cache hits on).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import networkx as nx
import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier
from repowise.core.ingestion.graph._centrality_cache import subgraph_signature
from repowise.core.pipeline.incremental import build_repo_graph, rebuild_graph_and_git


def _build_git_repo(tmp_path: Path) -> None:
    """Two python files co-committed 3x so co_changes edges exist (min_count=3)."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")

    # a.py and b.py are co-committed but unrelated statically: co_changes
    # edges are only added between files with no existing import edge.
    # c.py imports a.py so the graph also carries a real import edge.
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    (tmp_path / "c.py").write_text("from a import x\nz = x + 1\n")
    repo.index.add(["a.py", "b.py", "c.py"])
    repo.index.commit("feat: add modules a, b and c")

    for i in range(2, 5):
        (tmp_path / "a.py").write_text(f"x = {i}\n")
        (tmp_path / "b.py").write_text(f"y = {i}\n")
        repo.index.add(["a.py", "b.py"])
        repo.index.commit(f"chore: co-change a and b, round {i}")
    repo.close()


def _build_init_shaped_graph(tmp_path: Path, git_meta_map: dict) -> GraphBuilder:
    """Replicate the init pipeline's graph construction (ingestion phase +
    co-change augmentation), independent of the update-path helpers."""
    from repowise.core.ingestion.dynamic_hints import HintRegistry

    traverser = FileTraverser(tmp_path)
    parser = ASTParser()
    gb = GraphBuilder(tmp_path)
    for fi in traverser.traverse():
        gb.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    gb.build()
    # Framework edges: tech-stack detection on a bare two-file repo adds
    # nothing, but run it anyway to mirror the phase order.
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        gb.add_framework_edges([item.name for item in detect_tech_stack(tmp_path)])
    except Exception:
        pass
    gb.add_dynamic_edges(HintRegistry().extract_all(tmp_path))
    gb.add_co_change_edges(git_meta_map)
    return gb


def _edge_set(gb: GraphBuilder) -> set[tuple[str, str, str]]:
    g = gb.graph()
    return {(u, v, d.get("edge_type", "")) for u, v, d in g.edges(data=True)}


async def test_update_graph_converges_with_init_graph(tmp_path: Path) -> None:
    _build_git_repo(tmp_path)

    # Init shape: full git index feeds co-change edges for every file.
    init_indexer = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    _summary, init_meta = await init_indexer.index_repo("repo1")
    init_meta_map = {m["file_path"]: m for m in init_meta}
    init_gb = _build_init_shaped_graph(tmp_path, init_meta_map)

    # Sanity: the fixture actually produces a co_changes edge, otherwise
    # this test cannot detect the divergence it exists to prevent.
    assert any(t == "co_changes" for _, _, t in _edge_set(init_gb))

    # Update shape: one changed file, graph rebuilt by the update path.
    file_diffs = [SimpleNamespace(path="a.py", status="modified")]
    (_pf, _src, upd_gb, _structure, _count, _meta) = await rebuild_graph_and_git(
        tmp_path, file_diffs, cfg={}, exclude_patterns=[], git_tier="full"
    )

    assert set(init_gb.graph().nodes()) == set(upd_gb.graph().nodes())
    assert _edge_set(init_gb) == _edge_set(upd_gb)

    # The exact property the centrality cache keys on: identical file and
    # symbol subgraph signatures, so a first post-init update can hit.
    assert subgraph_signature(init_gb.file_subgraph()) == subgraph_signature(upd_gb.file_subgraph())
    assert subgraph_signature(init_gb.symbol_subgraph()) == subgraph_signature(
        upd_gb.symbol_subgraph()
    )


async def test_opt_out_update_does_not_touch_incremental_graph_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_git_repo(tmp_path)
    index_dir = tmp_path / ".repowise"
    index_dir.mkdir()
    seed = index_dir / "graph_seed_edges.pkl"
    resolutions = index_dir / "resolved_imports.pkl"
    seed.write_bytes(b"existing seed snapshot")
    resolutions.write_bytes(b"existing resolution snapshot")
    monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", "0")

    await rebuild_graph_and_git(
        tmp_path,
        [SimpleNamespace(path="a.py", status="modified")],
        cfg={},
        exclude_patterns=[],
        git_tier="full",
    )

    assert seed.read_bytes() == b"existing seed snapshot"
    assert resolutions.read_bytes() == b"existing resolution snapshot"


async def test_update_does_not_touch_incremental_caches_when_opted_out(
    tmp_path: Path, monkeypatch
) -> None:
    _build_git_repo(tmp_path)
    index = tmp_path / ".repowise"
    index.mkdir()
    seed_path = index / "graph_seed_edges.pkl"
    resolutions_path = index / "resolved_imports.pkl"
    seed_path.write_bytes(b"existing seed is untouched when opt-out")
    resolutions_path.write_bytes(b"existing resolutions are untouched when opt-out")
    monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", "0")

    await rebuild_graph_and_git(
        tmp_path,
        [SimpleNamespace(path="a.py", status="modified")],
        cfg={},
        exclude_patterns=[],
        git_tier="full",
    )

    assert seed_path.read_bytes() == b"existing seed is untouched when opt-out"
    assert resolutions_path.read_bytes() == b"existing resolutions are untouched when opt-out"


async def test_missing_patch_seed_is_invalidated_before_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / ".repowise" / "graph_seed_edges.pkl"
    cache.parent.mkdir()
    cache.write_bytes(b"stale snapshot")
    events: list[str] = []
    messages: list[str] = []
    graph_builder = SimpleNamespace(
        compute_metrics_parallel=AsyncMock(),
        update_co_change_edges=lambda _meta: None,
    )

    async def missing_seed(_repo_path: Path) -> None:
        assert cache.is_file()
        events.append("load_seed")
        return None

    def full_build(*_args, **kwargs):
        events.append("build_graph")
        assert kwargs["seed_edges"] is None
        assert not cache.exists()
        return [], {}, graph_builder, SimpleNamespace(), 0

    async def no_changed_files(*_args, **_kwargs):
        return []

    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph.build_repo_graph",
        full_build,
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph_seed.load_seed_edges",
        missing_seed,
    )

    async def no_previous_seed(_repo_path: Path) -> None:
        return None

    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph_seed.load_seed_edges_for_reconciliation",
        no_previous_seed,
    )
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.GitIndexer.index_changed_files",
        no_changed_files,
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.phases.git.label_co_change_structure",
        lambda *_args: None,
    )

    await rebuild_graph_and_git(
        tmp_path,
        [],
        cfg={},
        exclude_patterns=[],
        patch_graph=True,
        log=messages.append,
    )

    assert events == ["load_seed", "build_graph"]
    assert not cache.exists()
    assert any("no valid persisted edge snapshot" in message for message in messages)


async def test_full_fallback_restores_unparsed_rows_before_metric_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = nx.DiGraph()
    graph.add_node("b.py", file_path="b.py")
    previous_snapshot = {
        "nodes": [
            ("a.py::run", {"file_path": "a.py"}),
            ("b.py", {"file_path": "b.py"}),
        ],
        "edges": [("a.py::run", "b.py", {"edge_type": "calls"})],
    }
    builder = SimpleNamespace(
        graph=lambda: graph,
        traversed_file_paths={"a.py", "b.py"},
        patch_plan=SimpleNamespace(eligible=False, reason="parser fingerprint mismatch"),
    )
    parsed_files = [SimpleNamespace(file_info=SimpleNamespace(path="b.py"))]

    async def missing_seed(_repo_path: Path) -> None:
        return None

    async def prior_snapshot(_repo_path: Path) -> dict[str, list]:
        return previous_snapshot

    def full_build(*_args, **_kwargs):
        return parsed_files, {}, builder, SimpleNamespace(), 2

    async def no_git(*_args, **_kwargs) -> dict:
        return {}

    async def check_metrics(graph_builder, *_args, **_kwargs) -> None:
        assert graph_builder.graph().has_edge("a.py::run", "b.py")

    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph.build_repo_graph",
        full_build,
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph_seed.load_seed_edges",
        missing_seed,
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph_seed.load_seed_edges_for_reconciliation",
        prior_snapshot,
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph._index_git_metadata",
        no_git,
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.incremental.graph._prime_graph_metrics",
        check_metrics,
    )

    await rebuild_graph_and_git(
        tmp_path,
        [],
        cfg={},
        exclude_patterns=[],
        patch_graph=True,
    )


def test_dynamic_hint_failure_logs_warning_and_does_not_raise(tmp_path: Path) -> None:
    """Dynamic hint extraction errors must log a structured warning and continue best-effort."""
    logged_lines: list[str] = []

    def capture_log(msg: str) -> None:
        logged_lines.append(msg)

    (tmp_path / "main.py").write_text("x = 1\n")

    with patch(
        "repowise.core.ingestion.dynamic_hints.HintRegistry.extract_all",
        side_effect=RuntimeError("Simulated hint extraction crash"),
    ):
        _parsed_files, _source_map, graph_builder, _repo_structure, _count = build_repo_graph(
            repo_path=tmp_path,
            exclude_patterns=[],
            log=capture_log,
        )

    assert graph_builder is not None
    assert any(
        "Dynamic hint extraction failed" in line and "missing from this update" in line
        for line in logged_lines
    )


@pytest.mark.parametrize("with_document", [False, True])
def test_read_only_build_is_the_update_graph_and_writes_nothing(
    tmp_path: Path, monkeypatch, with_document: bool
) -> None:
    """The live commands' read-only build is the update path's graph, minus the writes."""
    _build_git_repo(tmp_path)
    if with_document:
        from repowise.core.ingestion.document_extractors import document_extractors
        from repowise.core.registry import plugins
        from tests.unit.ingestion.document_extractor_spawn_plugin import SpawnExtractor

        monkeypatch.setattr(document_extractors, "_impls", {})
        monkeypatch.setattr(document_extractors, "_chosen", None)
        monkeypatch.setattr(plugins, "load_plugins", lambda: [])
        monkeypatch.setenv(
            "REPOWISE_PLUGINS", "tests.unit.ingestion.document_extractor_spawn_plugin:register"
        )
        document_extractors.register("spawn-test-extractor", SpawnExtractor())
        (tmp_path / "spec.xyz").write_bytes(b"document source")
    index_dir = tmp_path / ".repowise"
    index_dir.mkdir()

    ro_pf, ro_src, ro_gb, _s, ro_count = build_repo_graph(
        tmp_path, [], collect_sources=True, read_only=True
    )
    assert list(index_dir.iterdir()) == []
    if with_document:
        assert ro_src["spec.xyz"] == b"# Extracted document\n\ndocument source\n"

    pf, src, gb, _s, count = build_repo_graph(tmp_path, [], collect_sources=True)
    assert list(index_dir.iterdir()), "the update path keeps its caches"

    assert (ro_count, ro_src) == (count, src)
    assert [p.file_info.path for p in ro_pf] == [p.file_info.path for p in pf]
    assert set(ro_gb.graph().nodes()) == set(gb.graph().nodes())
    assert _edge_set(ro_gb) == _edge_set(gb)
    if with_document:
        before = {p: p.read_bytes() for p in index_dir.rglob("*") if p.is_file()}
        build_repo_graph(tmp_path, [], collect_sources=True, read_only=True)
        assert before == {p: p.read_bytes() for p in index_dir.rglob("*") if p.is_file()}


@pytest.mark.parametrize(("value", "expected"), [(None, True), ("1", True), ("0", False)])
def test_incremental_graph_is_on_unless_turned_off(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    from repowise.core.pipeline.incremental.feature import incremental_graph_enabled

    if value is None:
        monkeypatch.delenv("REPOWISE_INCREMENTAL_GRAPH", raising=False)
    else:
        monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", value)
    assert incremental_graph_enabled() is expected

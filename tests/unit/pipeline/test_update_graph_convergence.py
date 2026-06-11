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

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier
from repowise.core.ingestion.graph._centrality_cache import subgraph_signature
from repowise.core.pipeline.incremental import rebuild_graph_and_git


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
    assert subgraph_signature(init_gb.file_subgraph()) == subgraph_signature(
        upd_gb.file_subgraph()
    )
    assert subgraph_signature(init_gb.symbol_subgraph()) == subgraph_signature(
        upd_gb.symbol_subgraph()
    )

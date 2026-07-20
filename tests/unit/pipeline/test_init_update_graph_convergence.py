"""Init-built and update-built graphs must be structurally identical.

The centrality disk cache keys on the exact node/edge sets of the file and
symbol subgraphs, so any init/update divergence makes the first post-init
update recompute betweenness from scratch. Two divergences are pinned here:

- The traverser must not index Repowise's own ``.repowise`` state dir; its
  contents differ between init (freshly created) and update (state.json,
  caches present), which changed the file subgraph between the two runs.
- Init must feed files to the GraphBuilder in walk order like the update
  rebuild does. Ambiguous reference resolution tie-breaks on insertion
  order, so the old completion-ordered collection let the same call edge
  resolve to a different candidate on init vs update.
"""

from __future__ import annotations

import asyncio
import time

import networkx as nx
import pytest

from repowise.core.ingestion import FileTraverser
from repowise.core.ingestion.graph._centrality_cache import subgraph_signature
from repowise.core.pipeline.incremental import build_repo_graph
from repowise.core.pipeline.phases.ingestion import _run_ingestion


def _write_fixture(repo) -> None:
    # Two same-named classes with the same method: an ambiguous member call
    # resolves by insertion-order tie-break, which is exactly what diverged
    # between init and update on PowerToys (QoiThumbnailProvider.GetThumbnail).
    (repo / "provider_a.py").write_text(
        "class Provider:\n    def get_thumbnail(self):\n        return 'a'\n",
        encoding="utf-8",
    )
    (repo / "provider_b.py").write_text(
        "class Provider:\n    def get_thumbnail(self):\n        return 'b'\n",
        encoding="utf-8",
    )
    (repo / "consumer.py").write_text(
        "def check(p):\n    return p.get_thumbnail()\n",
        encoding="utf-8",
    )
    (repo / "util.py").write_text(
        "def helper(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    (repo / "main.py").write_text(
        "from util import helper\n\n\ndef main():\n    return helper(2)\n",
        encoding="utf-8",
    )


@pytest.fixture()
def repo(tmp_path):
    _write_fixture(tmp_path)
    rw = tmp_path / ".repowise"
    rw.mkdir()
    (rw / "config.yaml").write_text("distill:\n  commands:\n    enabled: true\n")
    (rw / "state.json").write_text("{}")
    return tmp_path


def _scramble_completion_order(monkeypatch) -> None:
    """Make _build_file_info complete in reverse submission order.

    Emulates the thread-pool completion races the real init sees at repo
    scale; with only a handful of fixture files, futures would otherwise
    complete in order and hide any completion-order dependence.
    """
    original = FileTraverser._build_file_info
    counter = {"n": 0}

    def delayed(self, path):
        counter["n"] += 1
        time.sleep(max(0.0, 0.05 - counter["n"] * 0.005))
        return original(self, path)

    monkeypatch.setattr(FileTraverser, "_build_file_info", delayed)


def test_repowise_state_dir_not_traversed(repo):
    paths = [fi.path for fi in FileTraverser(repo).traverse()]
    assert paths, "fixture files should be traversed"
    assert not [p for p in paths if p.startswith(".repowise")]


def test_init_file_infos_in_walk_order(repo, monkeypatch):
    _scramble_completion_order(monkeypatch)
    _pf, file_infos, _rs, _sm, _gb, _stats, _tech = asyncio.run(
        _run_ingestion(
            repo,
            exclude_patterns=None,
            skip_tests=False,
            skip_infra=False,
            progress=None,
        )
    )
    paths = [fi.path for fi in file_infos]
    assert paths == sorted(paths)


def test_init_and_update_graphs_identical(repo, monkeypatch):
    _scramble_completion_order(monkeypatch)
    _pf, _fi, _rs, _sm, init_gb, _stats, _tech = asyncio.run(
        _run_ingestion(
            repo,
            exclude_patterns=None,
            skip_tests=False,
            skip_infra=False,
            progress=None,
        )
    )
    _pf2, _sm2, upd_gb, _rs2, _fc = build_repo_graph(repo, [], collect_sources=False)

    assert nx.utils.graphs_equal(init_gb.graph(), upd_gb.graph())

    # The exact property the centrality cache depends on: identical
    # signatures for both subgraph kinds means the first post-init update
    # hits instead of re-running Brandes.
    assert subgraph_signature(init_gb.file_subgraph()) == subgraph_signature(upd_gb.file_subgraph())
    assert subgraph_signature(init_gb.symbol_subgraph()) == subgraph_signature(
        upd_gb.symbol_subgraph()
    )

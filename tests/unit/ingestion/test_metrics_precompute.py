"""compute_metrics_parallel() equivalence — the update path now pre-computes
metrics with fan-out parallelism; values must match lazy computation."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file


def _parsed(tmp_path: Path, rel: str, content: str):
    abs_ = tmp_path / rel
    abs_.parent.mkdir(parents=True, exist_ok=True)
    abs_.write_text(content)
    fi = FileInfo(
        path=rel,
        abs_path=str(abs_),
        language="python",  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return parse_file(fi, content.encode("utf-8"))


_FILES = {
    "a.py": "from b import helper\nfrom c import C\n\ndef run():\n    return helper() + C().go()\n",
    "b.py": "def helper():\n    return 1\n",
    "c.py": "from b import helper\n\nclass C:\n    def go(self):\n        return helper()\n",
}


def _build(tmp_path: Path) -> GraphBuilder:
    gb = GraphBuilder(str(tmp_path))
    for rel, content in _FILES.items():
        gb.add_file(_parsed(tmp_path, rel, content))
    gb.build()
    return gb


class TestMetricsPrecompute:
    def test_parallel_matches_lazy(self, tmp_path: Path) -> None:
        lazy = _build(tmp_path)
        eager = _build(tmp_path)
        asyncio.run(eager.compute_metrics_parallel())
        # deterministic metrics must be identical
        assert eager.pagerank() == lazy.pagerank()
        assert eager.betweenness_centrality() == lazy.betweenness_centrality()
        assert eager.symbol_pagerank() == lazy.symbol_pagerank()
        assert eager.symbol_betweenness_centrality() == lazy.symbol_betweenness_centrality()

    def test_caches_populated(self, tmp_path: Path) -> None:
        gb = _build(tmp_path)
        assert gb._pagerank_cache is None
        asyncio.run(gb.compute_metrics_parallel())
        assert gb._pagerank_cache is not None
        assert gb._betweenness_cache is not None
        assert gb._symbol_pagerank_cache is not None
        assert gb._symbol_betweenness_cache is not None
        assert gb._community_cache is not None
        assert gb._symbol_community_cache is not None

    def test_reused_communities_give_new_nodes_file_community(self, tmp_path: Path, monkeypatch) -> None:
        """When rescore_due=False, communities are reused from disk and new symbols inherit their file's community (#5283)."""
        cache_dir = tmp_path / ".repowise"
        cache_dir.mkdir(parents=True, exist_ok=True)

        gb1 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb1.add_file(_parsed(tmp_path, rel, content))
        gb1.build()
        asyncio.run(gb1.compute_metrics_parallel())

        file_cd1 = gb1.community_detection()
        sym_cd1 = gb1.symbol_communities()
        flows1 = gb1.execution_flows()

        # Update: add a new function in b.py
        files2 = dict(_FILES)
        files2["b.py"] = _FILES["b.py"] + "\ndef new_helper():\n    return 42\n"

        # Ensure community detection algorithm is NOT called when rescore is not due
        def _forbid_detect(*args, **kwargs):
            raise AssertionError("File community detection must not run when rescore is not due")

        monkeypatch.setattr("repowise.core.analysis.communities.detect_file_communities", _forbid_detect)

        gb2 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=False)
        for rel, content in files2.items():
            gb2.add_file(_parsed(tmp_path, rel, content))
        gb2.build()

        file_cd2 = gb2.community_detection()
        assert file_cd2["b.py"] == file_cd1["b.py"]

        sym_cd2 = gb2.symbol_communities()
        # Existing symbols retain their community
        assert sym_cd2["b.py::helper"] == sym_cd1["b.py::helper"]
        # New symbol inherits its file's community
        assert "b.py::new_helper" in sym_cd2
        assert sym_cd2["b.py::new_helper"] == file_cd2["b.py"]

        # Execution flows are also reused
        flows2 = gb2.execution_flows()
        assert flows2.total_entry_points_scored == flows1.total_entry_points_scored

    def test_rescore_due_recomputes_communities(self, tmp_path: Path) -> None:
        """When rescore_due=True, communities recompute fresh rather than reusing (#5283)."""
        cache_dir = tmp_path / ".repowise"
        cache_dir.mkdir(parents=True, exist_ok=True)

        gb1 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb1.add_file(_parsed(tmp_path, rel, content))
        gb1.build()
        asyncio.run(gb1.compute_metrics_parallel())

        # Update with rescore_due=True
        gb2 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb2.add_file(_parsed(tmp_path, rel, content))
        gb2.build()

        assert gb2.community_detection() is not None
        assert gb2.symbol_communities() is not None

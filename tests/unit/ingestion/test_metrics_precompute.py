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

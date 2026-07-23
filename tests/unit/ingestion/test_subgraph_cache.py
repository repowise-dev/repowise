"""Unit tests for GraphBuilder subgraph caching (perf: 7 metric methods used
to rebuild file/symbol subgraphs per call).

Destination: tests/unit/ingestion/test_subgraph_cache.py
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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


def _builder(tmp_path: Path) -> GraphBuilder:
    gb = GraphBuilder(str(tmp_path))
    gb.add_file(_parsed(tmp_path, "a.py", "from b import helper\n\ndef run():\n    return helper()\n"))
    gb.add_file(_parsed(tmp_path, "b.py", "def helper():\n    return 1\n"))
    gb.build()
    return gb


class TestSubgraphCache:
    def test_file_subgraph_cached_instance(self, tmp_path: Path) -> None:
        gb = _builder(tmp_path)
        assert gb.file_subgraph() is gb.file_subgraph()

    def test_symbol_subgraph_cached_instance(self, tmp_path: Path) -> None:
        gb = _builder(tmp_path)
        assert gb.symbol_subgraph() is gb.symbol_subgraph()

    def test_contents_unchanged_by_caching(self, tmp_path: Path) -> None:
        gb = _builder(tmp_path)
        sub = gb.file_subgraph()
        assert set(sub.nodes) == {
            n for n, d in gb.graph().nodes(data=True)
            if d.get("node_type", "file") in ("file", "external")
        }
        assert not any(
            d.get("edge_type") == "co_changes" for _, _, d in sub.edges(data=True)
        )

    def test_file_subgraph_keeps_non_import_structural_edges(self, tmp_path: Path) -> None:
        gb = _builder(tmp_path)
        graph = gb.graph()
        graph.add_node("type_provider.py", node_type="file")
        graph.add_node("framework_entrypoint.py", node_type="file")
        graph.add_node("history_only.py", node_type="file")
        graph.add_edge("a.py", "type_provider.py", edge_type="type_use")
        graph.add_edge("framework_entrypoint.py", "a.py", edge_type="framework")
        graph.add_edge("a.py", "history_only.py", edge_type="co_changes")

        sub = gb.file_subgraph()

        assert sub["a.py"]["type_provider.py"]["edge_type"] == "type_use"
        assert sub["framework_entrypoint.py"]["a.py"]["edge_type"] == "framework"
        assert not sub.has_edge("a.py", "history_only.py")

    def test_invalidated_on_add_file(self, tmp_path: Path) -> None:
        gb = _builder(tmp_path)
        before = gb.file_subgraph()
        gb.add_file(_parsed(tmp_path, "c.py", "def extra():\n    return 2\n"))
        gb.build()
        after = gb.file_subgraph()
        assert after is not before
        assert "c.py" in after

    def test_invalidated_on_co_change_update(self, tmp_path: Path) -> None:
        gb = _builder(tmp_path)
        before_file = gb.file_subgraph()
        before_sym = gb.symbol_subgraph()
        gb.update_co_change_edges(
            {
                "a.py": {"file_path": "a.py", "co_change_files": [["b.py", 5]]},
            }
        )
        # co_changes edges are excluded from the file subgraph, but the cache
        # must still refresh — callers may rely on node attrs staying live.
        assert gb.file_subgraph() is not before_file
        assert gb.symbol_subgraph() is not before_sym

    def test_concurrent_metric_access_safe(self, tmp_path: Path) -> None:
        """Init computes metrics concurrently via asyncio.to_thread — the
        cache must never hand out a half-built subgraph."""
        gb = _builder(tmp_path)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(gb.pagerank) for _ in range(4)]
            futures += [pool.submit(gb.betweenness_centrality) for _ in range(4)]
            futures += [pool.submit(gb.symbol_pagerank) for _ in range(4)]
            results = [f.result() for f in futures]
        # pagerank dicts agree across threads
        assert results[0] == results[1] == results[2] == results[3]

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

    def test_communities_reuse_on_exact_signature_and_recompute_on_structural_change(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Exact-signature community reuse: body-only edits hit cache, structural edits match init exactly (#5283)."""
        cache_dir = tmp_path / ".repowise"
        cache_dir.mkdir(parents=True, exist_ok=True)

        gb1 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb1.add_file(_parsed(tmp_path, rel, content))
        gb1.build()
        asyncio.run(gb1.compute_metrics_parallel())

        file_cd1 = gb1.community_detection()
        sym_cd1 = gb1.symbol_communities()
        info1 = gb1.community_info()

        # Update 1: Body-only change in b.py (returns 42 instead of 1). Structure is identical.
        files_body = dict(_FILES)
        files_body["b.py"] = "def helper():\n    return 42\n"

        def _forbid_detect(*args, **kwargs):
            raise AssertionError("Community detection algorithm must not run on body-only edits")

        monkeypatch.setattr("repowise.core.analysis.communities.detect_file_communities", _forbid_detect)
        monkeypatch.setattr("repowise.core.analysis.communities.detect_symbol_communities", _forbid_detect)

        gb2 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=False)
        for rel, content in files_body.items():
            gb2.add_file(_parsed(tmp_path, rel, content))
        gb2.build()

        file_cd2 = gb2.community_detection()
        assert file_cd2 == file_cd1
        sym_cd2 = gb2.symbol_communities()
        assert sym_cd2 == sym_cd1
        info2 = gb2.community_info()
        assert set(info2.keys()) == set(info1.keys())

        # Mutating returned CommunityInfo does not corrupt cache
        info2[0].members.append("mutated.py")
        gb2_fresh_info = gb2.community_info()
        assert "mutated.py" not in gb2_fresh_info[0].members

        # Update 2: Structural change (add helpers.py, remove c.py).
        # Must recompute and match a fresh init run at that commit exactly.
        monkeypatch.undo()
        files_structural = {
            "a.py": "from helpers import helper\n\ndef run():\n    return helper()\n",
            "b.py": "from helpers import helper\n\ndef b_call():\n    return helper()\n",
            "helpers.py": "def helper():\n    return 100\n",
        }

        # Incremental update with cache
        gb_update = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=False)
        for rel, content in files_structural.items():
            gb_update.add_file(_parsed(tmp_path, rel, content))
        gb_update.build()

        # Fresh init run
        gb_fresh = GraphBuilder(str(tmp_path), centrality_cache_dir=None, rescore_due=True)
        for rel, content in files_structural.items():
            gb_fresh.add_file(_parsed(tmp_path, rel, content))
        gb_fresh.build()

        assert gb_update.community_detection() == gb_fresh.community_detection()
        assert gb_update.symbol_communities() == gb_fresh.symbol_communities()
        update_info = gb_update.community_info()
        fresh_info = gb_fresh.community_info()
        assert {k: v.members for k, v in update_info.items()} == {k: v.members for k, v in fresh_info.items()}

    def test_rescore_due_recomputes_communities(self, tmp_path: Path, monkeypatch) -> None:
        """When rescore_due=True, both file and symbol communities recompute fresh (#5283)."""
        from repowise.core.analysis import communities

        cache_dir = tmp_path / ".repowise"
        cache_dir.mkdir(parents=True, exist_ok=True)

        gb1 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb1.add_file(_parsed(tmp_path, rel, content))
        gb1.build()
        asyncio.run(gb1.compute_metrics_parallel())

        file_detect_calls = 0
        sym_detect_calls = 0
        real_detect_file = communities.detect_file_communities
        real_detect_sym = communities.detect_symbol_communities

        def counting_file(*args, **kwargs):
            nonlocal file_detect_calls
            file_detect_calls += 1
            return real_detect_file(*args, **kwargs)

        def counting_sym(*args, **kwargs):
            nonlocal sym_detect_calls
            sym_detect_calls += 1
            return real_detect_sym(*args, **kwargs)

        monkeypatch.setattr("repowise.core.analysis.communities.detect_file_communities", counting_file)
        monkeypatch.setattr("repowise.core.analysis.communities.detect_symbol_communities", counting_sym)

        # Update with rescore_due=True
        gb2 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb2.add_file(_parsed(tmp_path, rel, content))
        gb2.build()

        assert gb2.community_detection() is not None
        assert gb2.symbol_communities() is not None
        assert file_detect_calls == 1
        assert sym_detect_calls == 1

    def test_external_nodes_keep_no_community(self, tmp_path: Path) -> None:
        """External and framework nodes never get assigned a community or placed in community 0 (#5283)."""
        from repowise.core.analysis.communities import NO_COMMUNITY, stored_community_id

        cache_dir = tmp_path / ".repowise"
        cache_dir.mkdir(parents=True, exist_ok=True)

        gb1 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb1.add_file(_parsed(tmp_path, rel, content))
        gb1.build()
        # Seed cache
        gb1.community_detection()

        # Update run with an external node present in the graph
        gb2 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=False)
        for rel, content in _FILES.items():
            gb2.add_file(_parsed(tmp_path, rel, content))
        gb2.build()
        g2 = gb2.graph()
        ext_node = "external:requests"
        g2.add_node(ext_node, node_type="file", is_external=True)

        cd2 = gb2.community_detection()
        assert ext_node not in cd2
        assert stored_community_id(cd2, ext_node) == NO_COMMUNITY

    def test_execution_flows_cache_invalidation_and_reuse(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Execution flow report is reused on identical symbol structure and re-traced on changes (#5283)."""
        cache_dir = tmp_path / ".repowise"
        cache_dir.mkdir(parents=True, exist_ok=True)

        gb1 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=True)
        for rel, content in _FILES.items():
            gb1.add_file(_parsed(tmp_path, rel, content))
        gb1.build()
        flows1 = gb1.execution_flows()
        assert flows1 is not None

        from repowise.core.analysis import execution_flows

        trace_calls = 0
        real_trace = execution_flows.trace_execution_flows

        def counting_trace(*args, **kwargs):
            nonlocal trace_calls
            trace_calls += 1
            return real_trace(*args, **kwargs)

        monkeypatch.setattr(
            "repowise.core.analysis.execution_flows.trace_execution_flows", counting_trace
        )

        # Run 2: Same files and structure, rescore_due=False -> execution flows must hit cache
        gb2 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=False)
        for rel, content in _FILES.items():
            gb2.add_file(_parsed(tmp_path, rel, content))
        gb2.build()
        flows2 = gb2.execution_flows()
        assert flows2 is not None
        assert trace_calls == 0  # Reused from cache!

        # Run 3: Symbol structure changed -> cache misses and trace_execution_flows runs
        files3 = dict(_FILES)
        files3["b.py"] = _FILES["b.py"] + "\ndef extra_sym():\n    return 10\n"
        gb3 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=False)
        for rel, content in files3.items():
            gb3.add_file(_parsed(tmp_path, rel, content))
        gb3.build()
        flows3 = gb3.execution_flows()
        assert flows3 is not None
        assert trace_calls == 1  # Recomputed!

        # Run 4: Attribute change (edge confidence / edge type) -> cache misses and trace runs
        gb4 = GraphBuilder(str(tmp_path), centrality_cache_dir=cache_dir, rescore_due=False)
        for rel, content in files3.items():
            gb4.add_file(_parsed(tmp_path, rel, content))
        gb4.build()
        # Alter an edge confidence directly in the graph to simulate low confidence
        for _u, _v, d in gb4.graph().edges(data=True):
            if d.get("edge_type") == "calls":
                d["confidence"] = 0.1
                break
        flows4 = gb4.execution_flows()
        assert flows4 is not None
        assert trace_calls == 2  # Recomputed!

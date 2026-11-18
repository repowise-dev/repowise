"""compute_metrics_parallel() equivalence — the update path now pre-computes
metrics with fan-out parallelism; values must match lazy computation."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.analysis import execution_flows
from repowise.core.analysis.execution_flows import FlowConfig
from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.graph import _centrality_cache
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


_FLOW_FILES = {
    "main.py": (
        "from svc import load, check, save\n\n"
        "def main():\n    data = load()\n    check(data)\n    return save(data)\n"
    ),
    "svc.py": (
        "def load():\n    return read()\n\n"
        "def read():\n    return 1\n\n"
        "def check(x):\n    return validate(x)\n\n"
        "def validate(x):\n    return x\n\n"
        "def save(x):\n    return write(x)\n\n"
        "def write(x):\n    return x\n"
    ),
}


def _flow_builder(tmp_path: Path, files: dict[str, str] = _FLOW_FILES) -> GraphBuilder:
    gb = GraphBuilder(str(tmp_path), centrality_cache_dir=tmp_path / ".repowise")
    for rel, content in files.items():
        gb.add_file(_parsed(tmp_path, rel, content))
    gb.build()
    return gb


@pytest.fixture
def traces(monkeypatch) -> list[int]:
    """Counts real traces; a disk hit never reaches the tracer."""
    calls: list[int] = []
    real = execution_flows.trace_execution_flows

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(execution_flows, "trace_execution_flows", counting)
    return calls


def _traced_ids(report) -> set[str]:
    return {node for flow in report.flows for node in flow.trace}


def _set_confidence_into(gb: GraphBuilder, target: str, value: float) -> None:
    for _u, v, d in gb.graph().edges(data=True):
        if d.get("edge_type") == "calls" and v == target:
            d["confidence"] = value


class TestExecutionFlowDiskCache:
    def test_unchanged_rebuild_reuses_the_stored_report(self, tmp_path: Path, traces) -> None:
        first = _flow_builder(tmp_path).execution_flows()
        assert first.flows
        second = _flow_builder(tmp_path).execution_flows()
        assert len(traces) == 1
        assert second == first

    def test_deleted_function_is_retraced(self, tmp_path: Path, traces) -> None:
        first = _flow_builder(tmp_path).execution_flows()
        assert "svc.py::read" in _traced_ids(first)
        files = dict(_FLOW_FILES)
        files["svc.py"] = files["svc.py"].replace(
            "def load():\n    return read()\n\ndef read():\n    return 1\n",
            "def load():\n    return 1\n",
        )
        second = _flow_builder(tmp_path, files).execution_flows()
        assert len(traces) == 2
        assert "svc.py::read" not in _traced_ids(second)

    def test_confidence_drop_below_walkable_floor_retraces(self, tmp_path: Path, traces) -> None:
        gb = _flow_builder(tmp_path)
        _set_confidence_into(gb, "svc.py::read", 0.9)
        assert "svc.py::read" in _traced_ids(gb.execution_flows())
        # Still walkable: the signature holds, so the stored report is reused.
        gb = _flow_builder(tmp_path)
        _set_confidence_into(gb, "svc.py::read", 0.8)
        gb.execution_flows()
        assert len(traces) == 1
        gb = _flow_builder(tmp_path)
        _set_confidence_into(gb, "svc.py::read", 0.1)
        report = gb.execution_flows()
        assert len(traces) == 2
        assert "svc.py::read" not in _traced_ids(report)

    def test_config_bypasses_the_disk_cache(self, tmp_path: Path, traces) -> None:
        _flow_builder(tmp_path).execution_flows()
        gb = _flow_builder(tmp_path)
        gb._centrality_cache.lookup_flows = lambda **_: pytest.fail("config run read the cache")
        gb._centrality_cache.put_flows = lambda **_: pytest.fail("config run wrote the cache")
        gb.execution_flows(FlowConfig())
        assert len(traces) == 2

    def test_fresh_build_retraces_and_replaces(self, tmp_path: Path, traces) -> None:
        seeded = _flow_builder(tmp_path)
        seeded.execution_flows()
        signature = seeded._centrality_cache._flows[0]
        seeded._centrality_cache.put_flows(signature=signature, report="stale")
        fresh = _flow_builder(tmp_path)
        fresh.fresh_communities = True
        report = fresh.execution_flows()
        assert len(traces) == 2
        assert report != "stale"
        # The fresh trace replaced the stored one; the next ordinary run reuses it.
        assert _flow_builder(tmp_path).execution_flows() == report
        assert len(traces) == 2

    def test_cache_version_mismatch_is_a_miss(self, tmp_path: Path, traces, monkeypatch) -> None:
        _flow_builder(tmp_path).execution_flows()
        monkeypatch.setattr(
            _centrality_cache, "_CACHE_VERSION", _centrality_cache._CACHE_VERSION + 1
        )
        _flow_builder(tmp_path).execution_flows()
        assert len(traces) == 2

    def test_tracer_code_change_is_a_miss(self, tmp_path: Path, traces, monkeypatch) -> None:
        _flow_builder(tmp_path).execution_flows()
        monkeypatch.setattr(execution_flows, "_tracer_code_fingerprint", lambda: b"other")
        _flow_builder(tmp_path).execution_flows()
        assert len(traces) == 2

    def test_file_without_flows_key_is_a_miss(self, tmp_path: Path, traces) -> None:
        from repowise.core.cache_seal import dump_sealed_pickle

        _flow_builder(tmp_path).execution_flows()
        dump_sealed_pickle(
            tmp_path / ".repowise" / "centrality_cache.pkl",
            {"version": _centrality_cache._CACHE_VERSION},
            domain="centrality_cache.pkl",
        )
        _flow_builder(tmp_path).execution_flows()
        assert len(traces) == 2

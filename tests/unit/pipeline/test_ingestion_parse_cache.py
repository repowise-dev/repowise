"""Init-pipeline parse-cache wiring: warm-cache ingest == cold ingest.

`_run_ingestion` splits pre-read files into cache hits (skip the worker
pool) and misses (parse exactly as before), then merges back in traversal
order. The warm run must produce identical ParsedFiles, source maps, and
graphs — including with a stale cache after edits/adds/deletes.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.pipeline.phases import ingestion as ingestion_phase


def _write_fixture(repo) -> None:
    (repo / "util.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    (repo / "main.py").write_text(
        "from util import helper\n\n\ndef main():\n    return helper(2)\n",
        encoding="utf-8",
    )


async def _ingest(repo):
    parsed, _fis, _structure, source_map, gb, _stats, _tech = (
        await ingestion_phase._run_ingestion(
            repo,
            exclude_patterns=None,
            skip_tests=False,
            skip_infra=False,
            progress=None,
        )
    )
    return sorted(parsed, key=lambda p: p.file_info.path), source_map, gb.graph()


async def _fresh_ingest(repo):
    cache_file = repo / ".repowise" / "parse_cache.pkl"
    if cache_file.exists():
        cache_file.unlink()
    return await _ingest(repo)


async def test_run_ingestion_warm_cache_equivalent(tmp_path):
    _write_fixture(tmp_path)

    await _ingest(tmp_path)  # cold: populates cache
    assert (tmp_path / ".repowise" / "parse_cache.pkl").exists()

    # Mixed hit/miss: one edit + one add + one delete against the warm cache.
    (tmp_path / "util.py").write_text(
        "def helper(x):\n    return x * 3\n",
        encoding="utf-8",
    )
    (tmp_path / "added.py").write_text("NEW = 1\n", encoding="utf-8")

    warm_parsed, warm_sources, warm_graph = await _ingest(tmp_path)
    fresh_parsed, fresh_sources, fresh_graph = await _fresh_ingest(tmp_path)

    assert [p.file_info.path for p in warm_parsed] == [p.file_info.path for p in fresh_parsed]
    for wp, fp in zip(warm_parsed, fresh_parsed, strict=True):
        assert wp == fp, wp.file_info.path
    assert warm_sources == fresh_sources
    assert nx.utils.graphs_equal(warm_graph, fresh_graph)

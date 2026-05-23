#!/usr/bin/env python3
"""Benchmark the incremental fast→full upgrade vs a full re-index.

The ``repowise update --full`` upgrade reuses the persisted dependency graph
instead of rebuilding it. The structural work a full ``init`` re-run repeats —
and the upgrade skips — is the graph build (import/call/heritage resolution)
plus the centrality snapshot. Re-parsing and the FULL git tier happen either
way, so the *delta* between the two paths is precisely:

    full re-index structural cost  =  GraphBuilder.build() + file_metrics_snapshot()
    upgrade structural cost        =  rehydrate_graph_builder()  (SQL read, no resolve)

This script measures both on a synthetic repo and reports the ratio.

Usage::

    python scripts/benchmark_upgrade.py            # 2000 files
    python scripts/benchmark_upgrade.py --files 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from benchmark_large_repo import _git, generate_synthetic_repo  # noqa: E402


def _parse_and_build(repo: Path) -> tuple[object, list, dict[str, bytes], float]:
    """Parse the repo and build+resolve the graph. Returns (builder, parsed, src, secs)."""
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    traverser = FileTraverser(repo)
    file_infos = list(traverser.traverse())
    parser = ASTParser()
    parsed_files = []
    source_map: dict[str, bytes] = {}
    builder = GraphBuilder(repo)
    for fi in file_infos:
        try:
            src = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, src)
            parsed_files.append(parsed)
            source_map[fi.path] = src
            builder.add_file(parsed)
        except Exception:
            pass

    t0 = time.monotonic()
    builder.build()  # import/call/heritage resolution — the expensive pass
    builder.file_metrics_snapshot()  # pagerank + betweenness + community + degree
    structural_secs = time.monotonic() - t0
    return builder, parsed_files, source_map, structural_secs


async def _persist_and_rehydrate(repo: Path, builder: object) -> float:
    """Persist the graph then time a rehydrate from SQL (the upgrade path)."""
    from repowise.core.persistence import (
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import create_engine
    from repowise.core.pipeline import rehydrate_graph_builder
    from repowise.core.pipeline.persist import persist_graph_nodes

    db_path = repo / "bench.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        rec = await upsert_repository(session, name=repo.name, local_path=str(repo))
        repo_id = rec.id
        await persist_graph_nodes(session, repo_id, builder)
        # Edges (mirrors persist_pipeline_result).
        import json as _json

        from repowise.core.persistence import batch_upsert_graph_edges

        graph = builder.graph()
        edges = [
            {
                "source_node_id": u,
                "target_node_id": v,
                "imported_names_json": _json.dumps(d.get("imported_names", [])),
                "edge_type": d.get("edge_type", "imports"),
                "confidence": d.get("confidence", 1.0),
            }
            for u, v, d in graph.edges(data=True)
        ]
        if edges:
            await batch_upsert_graph_edges(session, repo_id, edges)

    async with get_session(sf) as session:
        t0 = time.monotonic()
        await rehydrate_graph_builder(session, repo_id, repo)
        rehydrate_secs = time.monotonic() - t0

    await engine.dispose()
    return rehydrate_secs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=2000, help="Number of source files.")
    parser.add_argument("--commits", type=int, default=20, help="Number of git commits.")
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="repowise-bench-upgrade-"))
    repo = tmp / "repo"
    n = generate_synthetic_repo(repo, args.files, args.commits)
    print(f"Generated {n} files. Measuring structural cost ...")

    builder, _parsed, _src, rebuild_secs = _parse_and_build(repo)
    rehydrate_secs = asyncio.run(_persist_and_rehydrate(repo, builder))

    speedup = (rebuild_secs / rehydrate_secs) if rehydrate_secs else float("inf")
    print(f"  full re-index structural (build+metrics): {rebuild_secs:.3f}s")
    print(f"  upgrade structural (rehydrate from SQL):  {rehydrate_secs:.3f}s")
    print(f"  speedup on the avoided work:              {speedup:.1f}x")

    out_dir = REPO_ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sha = _git(REPO_ROOT, "rev-parse", "--short", "HEAD")
    except Exception:
        sha = "nogit"
    out = out_dir / f"upgrade-{sha}.json"
    out.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "files": n,
                "rebuild_structural_secs": round(rebuild_secs, 3),
                "rehydrate_secs": round(rehydrate_secs, 3),
                "speedup": round(speedup, 1),
            },
            indent=2,
        )
    )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

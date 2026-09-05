#!/usr/bin/env python3
"""Benchmark scope-building for an already indexed repository.

This measures the cost of the cascade input used by ``update``:
``select_pages(select_all=True)`` through ``build_dependencies``.

It expects a repo that already has a repowise index on disk.

Usage::

    python scripts/benchmark_scope_selection.py --repo /path/to/indexed/repo
    python scripts/benchmark_scope_selection.py --repo /path/to/indexed/repo --runs 10
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


async def _load_inputs(repo_path: Path):
    from sqlalchemy import select

    from repowise.cli._repo_session import open_repo_db
    from repowise.core.generation import GenerationConfig
    from repowise.core.generation.scope import load_page_records
    from repowise.core.persistence import get_session
    from repowise.core.persistence.models import Page
    from repowise.core.pipeline import run_pipeline
    from repowise.core.repo_config import load_repo_config

    engine, sf, repo_id = await open_repo_db(repo_path, repo_name=repo_path.name)
    try:
        async with get_session(sf) as session:
            repo_rows = await session.execute(select(Page).where(Page.repository_id == repo_id))
            pages = list(repo_rows.scalars().all())
            records = load_page_records(pages)
    finally:
        await engine.dispose()

    result = await run_pipeline(repo_path, generate_docs=False)
    cfg = GenerationConfig.from_repo_config(load_repo_config(repo_path))
    return {
        "records": records,
        "graph_builder": result.graph_builder,
        "parsed_files": result.parsed_files,
        "config": cfg,
        "repo_name": repo_path.name,
    }


def _build_inputs(payload: dict):
    from repowise.core.generation.scope import build_dependencies
    from repowise.core.generation.selection.selector import SelectionInputs, select_pages

    graph_builder = payload["graph_builder"]
    parsed_files = payload["parsed_files"]
    cfg = payload["config"]
    # The selector needs the same in-memory graph features the scope builder uses.
    inputs = SelectionInputs(
        parsed_files=parsed_files,
        pagerank=graph_builder.pagerank(),
        betweenness=graph_builder.betweenness_centrality(),
        community=graph_builder.community_detection(),
        community_info=graph_builder.community_info(),
        sccs=list(graph_builder.strongly_connected_components()),
        git_meta_map=None,
        config=cfg,
        kg_modules=None,
    )
    t0 = time.perf_counter()
    selection = select_pages(inputs)
    select_secs = time.perf_counter() - t0

    t1 = time.perf_counter()
    deps = build_dependencies(
        parsed_files=parsed_files,
        graph_builder=graph_builder,
        config=cfg,
        kg_ctx=None,
        records=payload["records"],
        repo_name=payload["repo_name"],
    )
    deps_secs = time.perf_counter() - t1
    return select_secs, deps_secs, len(selection.module_groups), len(selection.scc_groups), deps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Path to an indexed repo.")
    parser.add_argument("--runs", type=int, default=7, help="Timed iterations.")
    args = parser.parse_args(argv)

    repo_path = args.repo.resolve()
    payload = asyncio.run(_load_inputs(repo_path))

    select_samples: list[float] = []
    deps_samples: list[float] = []
    module_count = scc_count = 0

    for i in range(args.runs + 1):
        select_secs, deps_secs, module_count, scc_count, _deps = _build_inputs(payload)
        # Warm-up run is discarded.
        if i == 0:
            continue
        select_samples.append(select_secs)
        deps_samples.append(deps_secs)

    def _summary(samples: list[float]) -> tuple[float, float, float]:
        return (
            statistics.median(samples) * 1000.0,
            max(samples) * 1000.0,
            min(samples) * 1000.0,
        )

    sel_med, sel_max, sel_min = _summary(select_samples)
    dep_med, dep_max, dep_min = _summary(deps_samples)

    print(f"repo: {repo_path}")
    print(f"module_groups: {module_count}  scc_groups: {scc_count}")
    print(f"select_pages(select_all=True): median {sel_med:.2f} ms  min {sel_min:.2f} ms  max {sel_max:.2f} ms")
    print(f"build_dependencies():           median {dep_med:.2f} ms  min {dep_min:.2f} ms  max {dep_max:.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

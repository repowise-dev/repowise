"""Deleted-file prune shared by every update writer."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from repowise.core.pipeline.phase_timing import PhaseTimings, timed

from ._common import LogFn


async def prune_deleted_files(
    session: Any,
    repo_id: str,
    repo_path: Any,
    graph_builder: Any,
    live_paths: Iterable[str],
    *,
    health_report: Any,
    skip: Callable[[str, Exception], None],
    log: LogFn,
    degraded: list[str] | None,
    timings: PhaseTimings | None,
) -> None:
    """Drop file-scoped rows for deleted files, then rebuild the queues built from them.

    Shared by every update writer. Liveness is asked of disk and git, never of
    this run's parse (see prune_deleted_file_rows).

    Call it last in the session: the steps before it upsert, and the git step
    indexes deleted paths too, so an earlier prune would be undone.

    The graph's file nodes join *live_paths* because resolver passes mint
    file-shaped nodes that name no file (`external:`, `framework:`, Spring SPI
    sources); they fail every liveness test, and only changed files' edges are
    rebuilt afterwards. A graph that cannot be read skips the prune.
    """
    pruned = 0
    try:
        from repowise.core.pipeline.persist import prune_deleted_file_rows

        live_hint = set(live_paths) | {
            node
            for node, data in graph_builder.graph().nodes(data=True)
            if data.get("node_type", "file") == "file"
        }
        with timed(timings, "persist.prune"):
            pruned, refusals = await prune_deleted_file_rows(
                session, repo_id, repo_path, live_hint=live_hint
            )
        if pruned:
            log(f"Pruned rows for [cyan]{pruned}[/cyan] deleted file(s)")
        for refusal in refusals:
            log(f"[yellow]{refusal}[/yellow]")
            if degraded is not None:
                degraded.append(refusal)
    except Exception as exc:
        skip("Deleted-file prune", exc)

    # The performance and refactoring queues are materialized from the health
    # findings the prune just deleted; without this they keep serving a cause
    # whose evidence is gone, on a file the store no longer claims exists.
    if not pruned:
        return
    try:
        from repowise.core.persistence.crud import (
            finalize_performance_opportunities,
            finalize_refactoring_opportunities,
        )
        from repowise.core.pipeline.persist import _analyzed_commit

        analyzed_commit = await _analyzed_commit(session, repo_id)
        await finalize_performance_opportunities(
            session,
            repo_id,
            analyzed_commit=analyzed_commit,
            plan_policy=getattr(health_report, "performance_plan_policy", None),
            execution_roles=getattr(health_report, "execution_roles", None),
        )
        await finalize_refactoring_opportunities(session, repo_id, analyzed_commit=analyzed_commit)
    except Exception as exc:
        skip("Queue rebuild after prune", exc)

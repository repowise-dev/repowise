"""Cost-tracker construction shared across CLI commands."""

from __future__ import annotations

import os
from pathlib import Path

from repowise.core.generation.cost_tracker import CostTracker

# Env var that disables DB-backed cost tracking entirely (issue #326). When set
# to a truthy value, ``build_cost_tracker`` returns an in-memory tracker that
# never opens a second engine on ``wiki.db`` — so cost persistence can never
# contend with the generation writer. Mirrors the ``--no-cost-tracking`` flag.
NO_COST_TRACKING_ENV = "REPOWISE_NO_COST_TRACKING"

# Cost rows are best-effort telemetry, not part of the index. The DB-backed
# tracker opens a *second* engine on ``wiki.db``, so during generation its
# per-call ``INSERT INTO llm_costs`` loses WAL's single-writer race against the
# held page writer. With the normal 30s ``busy_timeout`` each contended insert
# stalls generation for the full window (issue #326). A short timeout makes the
# insert fail fast; ``CostTracker._persist`` swallows the error and drops the
# row, so a contended cost write costs at most this many ms instead of 30s.
_COST_TRACKER_BUSY_TIMEOUT_MS = 2000

_TRUTHY = {"1", "true", "yes", "on"}


def cost_tracking_disabled(no_cost_tracking_flag: bool = False) -> bool:
    """Return whether DB-backed cost tracking should be skipped.

    Disabled when the ``--no-cost-tracking`` flag is passed *or* the
    ``REPOWISE_NO_COST_TRACKING`` env var is truthy.
    """
    if no_cost_tracking_flag:
        return True
    return os.environ.get(NO_COST_TRACKING_ENV, "").strip().lower() in _TRUTHY


async def make_cost_tracker(repo_path: Path, repo_name: str) -> CostTracker:
    """Build a DB-backed :class:`CostTracker` for the repo-local database.

    Resolves (or creates) the repository row so every LLM call made during
    this run is persisted to the ``llm_costs`` table. The engine is
    intentionally left open: it lives for the duration of generation and is
    disposed later by the persistence path that reuses the same database.

    The engine uses a short ``busy_timeout`` so a cost insert that loses the
    write race against the generation writer fails fast and is dropped rather
    than stalling generation for the full 30s window (issue #326).
    """
    from repowise.cli._repo_session import open_repo_db

    _engine, sf, repo_id = await open_repo_db(
        repo_path,
        repo_name=repo_name,
        busy_timeout_ms=_COST_TRACKER_BUSY_TIMEOUT_MS,
    )
    return CostTracker(session_factory=sf, repo_id=repo_id)


def build_cost_tracker(
    repo_path: Path,
    repo_name: str,
    *,
    no_cost_tracking: bool = False,
) -> CostTracker:
    """Construct a cost tracker, falling back to an in-memory one on failure.

    Returns an in-memory :class:`CostTracker` (no second engine, no DB writes)
    when cost tracking is disabled via the ``--no-cost-tracking`` flag or the
    ``REPOWISE_NO_COST_TRACKING`` env var. In-memory tracking still powers the
    live cost readout; only historical ``repowise costs`` rows are skipped.
    """
    if cost_tracking_disabled(no_cost_tracking):
        return CostTracker()

    from repowise.cli.helpers import run_async

    try:
        return run_async(make_cost_tracker(repo_path, repo_name))
    except Exception:
        return CostTracker()

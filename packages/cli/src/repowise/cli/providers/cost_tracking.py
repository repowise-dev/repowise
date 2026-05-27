"""Cost-tracker construction shared across CLI commands."""

from __future__ import annotations

from pathlib import Path

from repowise.core.generation.cost_tracker import CostTracker


async def make_cost_tracker(repo_path: Path, repo_name: str) -> CostTracker:
    """Build a DB-backed :class:`CostTracker` for the repo-local database.

    Resolves (or creates) the repository row so every LLM call made during
    this run is persisted to the ``llm_costs`` table. The engine is
    intentionally left open: it lives for the duration of generation and is
    disposed later by the persistence path that reuses the same database.
    """
    from repowise.cli._repo_session import open_repo_db

    _engine, sf, repo_id = await open_repo_db(repo_path, repo_name=repo_name)
    return CostTracker(session_factory=sf, repo_id=repo_id)


def build_cost_tracker(repo_path: Path, repo_name: str) -> CostTracker:
    """Construct a cost tracker, falling back to an in-memory one on failure."""
    from repowise.cli.helpers import run_async

    try:
        return run_async(make_cost_tracker(repo_path, repo_name))
    except Exception:
        return CostTracker()

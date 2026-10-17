"""Inputs and per-run state for one single-repo ``repowise update``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.pipeline import PhaseTimings


@dataclass(frozen=True)
class UpdateOptions:
    """The arguments of one :func:`run_update` call, exactly as passed."""

    path: str | None
    provider_name: str | None
    model: str | None
    since: str | None
    reasoning: str | None
    cascade_budget: int | None
    dry_run: bool
    workspace: bool
    no_workspace: bool
    repo_alias: str | None
    index_only: bool = False
    docs_flag: bool | None = None
    full: bool = False
    yes: bool = False
    agents_md: bool | None = None
    concurrency: int = 10
    no_cost_tracking: bool = False
    verbose: bool = False
    progress: str = "rich"
    skip_cross_repo_hooks: bool = False
    include_working_tree: bool = False


@dataclass
class UpdateRun:
    """What one run has learned so far; each stage reads and fills it in order."""

    start: float
    timings: PhaseTimings
    emitter: Any = None
    repo_path: Path | None = None

"""Inputs and per-run state for one single-repo ``repowise update``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.pipeline import PhaseTimings

from .config_changes import ConfigChanges


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

    # Preflight: stored state, the resolved mode and the commit range.
    state: dict = field(default_factory=dict)
    cfg: dict = field(default_factory=dict)
    index_only: bool = False
    base_ref: str | None = None
    head: str | None = None
    config: ConfigChanges | None = None
    prev_renderer_fp: str | None = None
    curr_renderer_fp: str = ""
    renderer_changed: bool = False

    # Change detection.
    detector: Any = None
    working_tree_diffs: list = field(default_factory=list)
    analyzer_changed: bool = False
    history_stale: bool = False
    git_is_current: bool = False
    extraction_changed: bool = False
    scanner_changed: bool = False
    stale_db_paths: list[str] = field(default_factory=list)
    stale_deterministic_ids: set[str] = field(default_factory=set)
    file_diffs: list = field(default_factory=list)

    # Index: the rebuilt graph, the page plan and the partial analyses.
    # Each best-effort step that fails appends here; shown in the completion panel.
    degraded: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    git_decay_map: dict[str, dict] = field(default_factory=dict)
    full_git_summaries: list[Any] = field(default_factory=list)
    working_tree_only: bool = False
    head_ts: float | None = None
    parsed_files: Any = None
    source_map: Any = None
    graph_builder: Any = None
    repo_structure: Any = None
    file_count: int = 0
    git_meta_map: Any = None
    cascade_budget: int | None = None
    affected: Any = None
    partial_health_report: Any = None
    dead_code_report: Any = None
    doc_drift_report: Any = None
    persisted_git_meta: Any = None
    knowledge_graph_result: Any = None


@dataclass
class DocsPass:
    """State of the model-backed half of a docs update."""

    config: Any
    provider: Any
    cost_tracker: Any
    new_decision_markers: list = field(default_factory=list)
    session_decisions: list = field(default_factory=list)
    # Decision records touched by evolution, surfaced in the completion panel.
    decisions_evolved: int = 0
    decision_vector_store: Any = None
    prior_pages: dict = field(default_factory=dict)
    generated_pages: list = field(default_factory=list)
    full_stats: dict = field(default_factory=dict)
    db_total_pages: int = 0
    # Range-scoped persist steps that failed; they become the repair marker.
    failed_steps: list[str] = field(default_factory=list)

"""Run state for one ``run_pipeline`` call and the setup steps that build it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.registry import HookProgressCallback
from repowise.core.registry.plugins import load_plugins


@dataclass
class PipelineRun:
    """Options of one pipeline run plus the outputs each stage leaves for the next."""

    repo_path: Path
    start: float
    mode: OrchestratorMode
    generate_docs: bool
    git_tier: Any
    progress: Any
    checkpointer: Any
    commit_depth: int
    follow_renames: bool
    skip_tests: bool
    skip_infra: bool
    exclude_patterns: list[str] | None
    include_submodules: bool
    include_nested_repos: bool
    derive_environment_facts: bool
    llm_client: Any
    embedder: Any
    vector_store: Any
    concurrency: int
    test_run: bool
    resume: bool
    generation_config: Any
    wiki_style: str | None
    existing_kg_fingerprint: str | None
    on_page_ready: Any
    resume_controller: Any
    coverage_report_paths: list[Path] | None

    # Index phase outputs.
    skip_index: bool = False
    parsed_files: list[Any] = field(default_factory=list)
    file_infos: list[Any] = field(default_factory=list)
    repo_structure: Any = None
    source_map: dict[str, bytes] = field(default_factory=dict)
    graph_builder: Any = None
    traversal_stats: Any = None
    tech_items: list[Any] = field(default_factory=list)
    git_summary: Any = None
    git_metadata_list: list[dict] = field(default_factory=list)
    git_meta_map: dict[str, dict] = field(default_factory=dict)
    external_systems: list[dict] = field(default_factory=list)

    # Analysis phase outputs. The ``gen_`` reports feed generation and the KG:
    # rehydrated on a resumed run, the freshly computed ones otherwise.
    skip_analysis: bool = False
    dead_code_report: Any = None
    health_report: Any = None
    decision_report: Any = None
    doc_drift_report: Any = None
    gen_dead_code_report: Any = None
    gen_decision_report: Any = None
    tech_stack_dicts: list[dict] = field(default_factory=list)
    knowledge_graph_result: Any = None

    # Generation phase outputs; see ``PipelineResult`` for the two page-id sets.
    generated_pages: list[Any] | None = None
    authoritative_page_types: set[str] = field(default_factory=set)
    preserved_page_ids: set[str] = field(default_factory=set)
    execution_flow_report: Any = None


def hooked_progress(progress: Any) -> HookProgressCallback:
    """Wrap *progress* so registered pipeline hooks fire around each phase.

    Plugins load first so the hooks they register are among them.
    """
    load_plugins()
    return HookProgressCallback(progress)


async def start_checkpointer(job_store: Any, repo_path: Path) -> Any:
    """Start phase checkpointing for crash-resume when a JobStore is supplied, else None."""
    if job_store is None:
        return None
    from repowise.core.pipeline.checkpoint import PhaseCheckpointer

    checkpointer = PhaseCheckpointer(job_store, str(repo_path))
    await checkpointer.start()
    return checkpointer


def attach_cost_tracker(llm_client: Any, cost_tracker: Any) -> None:
    if cost_tracker is not None and llm_client is not None and hasattr(llm_client, "_cost_tracker"):
        llm_client._cost_tracker = cost_tracker

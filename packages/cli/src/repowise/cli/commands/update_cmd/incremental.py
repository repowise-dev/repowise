"""Thin CLI delegators to :mod:`repowise.core.pipeline.incremental`.

These wrap the core incremental rebuild/analysis helpers so the update flow
(and the config-triggered re-score path) share one traverse/parse/build step,
routing core log output through the CLI ``console``.
"""

from __future__ import annotations

from typing import Any

from repowise.cli.helpers import console, run_async


def _build_update_vector_store(repo_path: Any, cfg: dict) -> Any | None:
    """Build the shared page/decision vector store for the update path.

    Phase-2 follow-up + Phase-3 requirement: ``repowise update`` historically
    upserted decisions *without* a vector store, so semantic dedup, decision
    search visibility, and supersession detection were all off on incremental
    runs. We mirror ``init``'s store construction (LanceDB at
    ``.repowise/lancedb`` so previously-embedded decisions are matchable; the
    in-memory store is a degraded fallback that only sees this run's vectors).
    Returns ``None`` on any failure — the decision upsert still works without it.
    """
    try:
        from repowise.cli.providers import build_embedder, build_vector_store, resolve_embedder

        embedder = build_embedder(resolve_embedder(cfg.get("embedder")))
        return build_vector_store(repo_path, embedder)
    except Exception:
        return None


def _build_filtered_changed_paths(file_diffs: list, exclude_patterns: list[str]) -> list[str]:
    """Extract paths from file_diffs, filtering out excluded patterns."""
    from repowise.core.pipeline.incremental import build_filtered_changed_paths

    return build_filtered_changed_paths(file_diffs, exclude_patterns)


def _build_repo_graph(
    repo_path: Any,
    exclude_patterns: list[str],
    *,
    collect_sources: bool = False,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
) -> tuple[list, dict[str, bytes], Any, Any, int]:
    """Traverse + parse the repo and build the graph (+ framework-aware edges).

    Delegates to :mod:`repowise.core.pipeline.incremental` — the logic moved
    to core so workspace updates can reuse the incremental path. Shared by
    the incremental rebuild path (:func:`_rebuild_graph_and_git`) and the
    config-triggered re-score path (:func:`_run_full_health_rescore`).
    """
    from repowise.core.pipeline.incremental import build_repo_graph

    return build_repo_graph(
        repo_path,
        exclude_patterns,
        collect_sources=collect_sources,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        log=console.print,
    )


def _rebuild_graph_and_git(
    repo_path: Any,
    file_diffs: list,
    cfg: dict,
    exclude_patterns: list[str],
    git_tier: str | None = None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    idle_decay_sink: dict[str, dict] | None = None,
) -> tuple[list, dict[str, bytes], Any, Any, int, dict[str, dict]]:
    """Re-traverse + parse the repo, rebuild the graph (+ framework edges), and
    re-index git metadata for the changed files.

    ``idle_decay_sink``, when provided, is filled with decay-only partial rows
    for idle files whose time-decayed history the anchor advance recovered
    (issue #728); the caller feeds them straight to the git persist step.

    ``git_tier`` is the persisted ``state.json:git_tier`` value: a fast-mode
    (ESSENTIAL) repo must not pay per-file blame on every update for signals
    its index never had. Unknown/missing values fall back to FULL, matching
    the historical behavior for legacy state files.

    ``include_submodules`` / ``include_nested_repos`` are likewise read from
    state.json: a repo indexed with ``init --include-submodules`` must not
    silently drop its submodule files on every incremental update. Missing
    keys fall back to False (legacy behavior).

    Delegates to :mod:`repowise.core.pipeline.incremental` — the logic moved
    to core so workspace updates can reuse the incremental path.

    Returns ``(parsed_files, source_map, graph_builder, repo_structure,
    file_count, git_meta_map)``.
    """
    from repowise.core.pipeline.incremental import rebuild_graph_and_git

    return run_async(
        rebuild_graph_and_git(
            repo_path,
            file_diffs,
            cfg,
            exclude_patterns,
            git_tier=git_tier,
            include_submodules=include_submodules,
            include_nested_repos=include_nested_repos,
            idle_decay_sink=idle_decay_sink,
            log=console.print,
        )
    )


def _refresh_knowledge_graph(
    repo_path: Any,
    parsed_files: list,
    graph_builder: Any,
    repo_structure: Any,
    git_meta_map: dict,
    dead_code_report: Any,
    prior_fingerprint: str | None,
) -> Any | None:
    """Rebuild the KG skeleton + curation when the graph shape changed.

    Delegates to :mod:`repowise.core.pipeline.incremental`. Returns ``None``
    when the graph fingerprint is unchanged (artifact already current) or the
    rebuild failed (keep the prior artifact).
    """
    from repowise.core.pipeline.incremental import refresh_knowledge_graph

    return run_async(
        refresh_knowledge_graph(
            repo_path,
            parsed_files,
            graph_builder,
            repo_structure,
            git_meta_map,
            dead_code_report,
            prior_fingerprint=prior_fingerprint,
            log=console.print,
        )
    )


def _run_partial_analysis(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    parsed_files: list,
    file_diffs: list,
) -> tuple[Any, Any]:
    """Run partial code-health + dead-code analysis for the changed files.

    Delegates to :mod:`repowise.core.pipeline.incremental` — the logic moved
    to core so workspace updates can reuse the incremental path.

    Returns ``(partial_health_report, dead_code_report)`` — either may be
    ``None`` if its analysis failed (both are best-effort).
    """
    from repowise.core.pipeline.incremental import run_partial_analysis

    return run_partial_analysis(
        repo_path,
        graph_builder,
        git_meta_map,
        parsed_files,
        file_diffs,
        log=console.print,
    )

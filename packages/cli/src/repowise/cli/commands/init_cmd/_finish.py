"""After generation: persistence, config and state, editor setup and the completion panel."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli.commands._index_outcome import record_index_outcome, refresh_read_snapshots
from repowise.cli.editor_setup import register_editor_clients, write_editor_project_files
from repowise.cli.helpers import (
    config_fingerprint,
    console,
    get_head_commit,
    head_commit_ts,
    load_config,
    load_state,
    run_async,
    save_config_partial,
    save_state,
)
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.cli.ui import (
    BRAND_STYLE,
    OK,
    OWL_SPINNER,
    MaybeCountColumn,
    RichProgressCallback,
    print_phase_header,
)
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION
from repowise.core.decision_lanes import record_decision_lanes
from repowise.core.docs_mode import docs_mode_state_fields
from repowise.core.generation.styles import DEFAULT_STYLE
from repowise.core.repo_config import config_dependency_fingerprints

from ._ingest import decision_llm_client
from ._interactive import offer_distill_rewrite_hook, offer_hook_install
from ._options import InitRun
from .persistence import (
    apply_git_history_coverage_state,
    git_tier_for_run_mode,
    keep_prior_documentation,
    persist_result,
    save_full_state_and_config,
)
from .reporting import show_completion


def persist_index(run: InitRun) -> None:
    """Save to the database and build the search index."""
    print_phase_header(
        console,
        run.total_phases,
        run.total_phases,
        "Persistence",
        "Saving to database and building search index",
    )

    # The index run's columns, minus cost.
    recorder = run.recorder
    with Progress(
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as persist_bar:
        persist_callback = recorder.rebind(RichProgressCallback(persist_bar, console))
        # Indeterminate, announced so the stage is never silent.
        persist_callback.on_phase_start("persist", None)
        run_async(persist_result(run.result, run.repo_path, persist_callback, recorder.table))
        persist_callback.on_phase_done("persist")
    # Persistence has its own callback; fold its warnings into the run record.
    run.run_warnings.extend(persist_callback.warnings)
    # Read last: every stage writes into the one table.
    recorder.table.stop("run")
    run.phase_timings = recorder.timings
    console.print(f"  [{OK}]✓[/] Database updated")


def catch_up_savings(repo_path: Path) -> None:
    """Bank the token savings agents recorded before the first index. Best-effort.

    Init only: its cost scales with agent history, too much for every update.
    """
    try:
        from repowise.core.savings.transcript import sync_transcript_savings

        outcome = sync_transcript_savings(repo_path)
    except Exception:
        return
    if outcome.recorded:
        console.print(
            f"  [{OK}]✓[/] Recovered {outcome.saved_input_tokens:,} saved tokens "
            f"from agent history"
        )


def save_run_config(run: InitRun) -> None:
    """Record this run's choices in config.yaml so later runs keep them."""
    opts = run.opts
    repo_path = run.repo_path
    # Defaults stay unrecorded; only overrides are written.
    if not opts.onboarding:
        save_config_partial(repo_path, enable_onboarding=False)

    # 0 (a refusal) is recorded; only None stays unwritten.
    if run.max_file_pages is not None and run.max_file_pages != run.config.get("max_file_pages"):
        save_config_partial(repo_path, max_file_pages=run.max_file_pages)

    # Written before the config fingerprint. A template wiki has no style or
    # language, and recording one would make `restyle` skip it.
    if opts.wiki_style != DEFAULT_STYLE and not run.effective_index_only:
        save_config_partial(repo_path, wiki_style=opts.wiki_style)

    if run.language != run.config.get("language", "en") and not run.effective_index_only:
        save_config_partial(repo_path, language=run.language)

    if opts.commit_limit is not None:
        save_config_partial(repo_path, commit_limit=run.resolved_commit_limit)


def register_clients(run: InitRun) -> None:
    """Global client registration, then the workspace's distill verdict."""
    from repowise.cli.editor_integrations.global_mcp import confirm_global_mcp

    if confirm_global_mcp(
        console,
        run.repo_path,
        yes=run.opts.yes,
        no_editor_setup=not run.opts.editor_setup,
    ):
        register_editor_clients(console, run.repo_path)

    # Before the config fingerprint: left unwritten, the first update writes it,
    # sees a fingerprint change and re-scores every file.
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    inherit_workspace_distill_verdict(run.repo_path)


def _docs_mode(run: InitRun) -> str:
    """``llm``, ``deterministic`` (templates) or ``none`` (fast mode)."""
    if run.opts.run_mode == "fast":
        return "none"
    if not run.effective_index_only and run.provider is not None:
        return "llm"
    return "deterministic"


def _search_scope(run: InitRun) -> dict[str, Any]:
    """The search leg of the scope: what works now, and the command that adds the rest."""
    from repowise.cli.providers import semantic_search_command, semantic_search_status

    result = run.result
    if not result.generated_pages:
        # Fast mode has no pages for reindex to embed.
        return {"full_text": "unavailable", "semantic": "unavailable", "next_command": None}
    semantic = semantic_search_status(run.scope_embedder, getattr(result, "embed_failed_pages", 0))
    return {
        "full_text": "available",
        "semantic": semantic,
        "next_command": (
            semantic_search_command(run.repo_path, run.scope_embedder)
            if semantic == "unavailable"
            else None
        ),
    }


def _stamp_scope(run: InitRun, base_state: dict[str, Any], docs_mode: str) -> None:
    from repowise.core.generation.selection import count_documentable_files
    from repowise.core.index_scope import dropped_files_scope, file_page_scope, stamp_index_scope

    result = run.result
    run_mode = run.opts.run_mode
    provider = run.provider
    scope_embedder = run.scope_embedder
    unavailable = []
    if getattr(result, "health_report", None) is None:
        unavailable.append("health")
    skipped = ["generation"] if run_mode == "fast" else []
    stamp_index_scope(
        base_state,
        {"commit_limit": run.resolved_commit_limit, "max_file_pages": run.max_file_pages},
        run_mode=run_mode,
        content_provenance={"none": "none", "deterministic": "template", "llm": "model"}[
            docs_mode
        ],
        git_tier=git_tier_for_run_mode(run_mode),
        git_commit_cap=run.resolved_commit_limit,
        dropped_files=dropped_files_scope(getattr(result, "traversal_stats", None)),
        test_run=run.opts.test_run and docs_mode == "llm",
        file_pages={
            "configured_cap": run.max_file_pages,
            **(
                result.generation_scope
                if getattr(result, "generation_scope", None)
                else file_page_scope(
                    configured_cap=run.max_file_pages,
                    eligible=count_documentable_files(result.parsed_files),
                    generated_pages=result.generated_pages,
                )
            ),
        },
        analysis={"unavailable": unavailable, "skipped": skipped},
        provider={
            "name": provider.provider_name if provider is not None else None,
            "model": provider.model_name if provider is not None else None,
            "embedder": scope_embedder,
            "reused": False,
            "model_cost_possible": docs_mode == "llm",
        },
        search=_search_scope(run),
        upgrade={
            "status": "pending" if docs_mode != "llm" else "not_applicable",
            "retryable": docs_mode != "llm",
            "completed_stages": [],
            "next_stage": "git_backfill" if run_mode == "fast" else "generation",
        },
    )


def _base_state(run: InitRun) -> dict[str, Any]:
    """state.json for this run, before the mode-specific stamps.

    ``last_sync_commit`` is written in every mode so ``repowise update`` has a
    baseline to diff against.
    """
    repo_path = run.repo_path
    result = run.result
    run_mode = run.opts.run_mode
    head = get_head_commit(repo_path)
    prior_state = load_state(repo_path)
    base_state = load_state(repo_path)
    base_state["last_sync_commit"] = head
    docs_mode = _docs_mode(run)
    base_state.update(docs_mode_state_fields(docs_mode))
    base_state["total_pages"] = len(getattr(result, "generated_pages", None) or [])
    # So a later --resume keeps this git tier.
    base_state["run_mode"] = run_mode
    base_state["git_tier"] = git_tier_for_run_mode(run_mode)
    apply_git_history_coverage_state(base_state, result)
    _stamp_scope(run, base_state, docs_mode)
    record_decision_lanes(
        base_state, repo_path, ran_with_model=decision_llm_client(run) is not None
    )
    keep_prior_documentation(base_state, prior_state, run_mode)
    base_state["include_submodules"] = run.opts.include_submodules
    if run.phase_timings:
        base_state["phase_timings"] = run.phase_timings
    # Never inherit the previous run's degradation report.
    base_state.pop("degraded", None)
    if run.run_warnings:
        base_state["degraded"] = run.run_warnings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        base_state["knowledge_graph"] = build_kg_state(kg)
        save_knowledge_graph_json(repo_path, kg)
    return base_state


def _save_index_only_state(run: InitRun, base_state: dict[str, Any]) -> None:
    """Config and state for a run with no model, which skips save_config()."""
    from repowise.cli.providers.embedders import resolve_embedding_model

    repo_path = run.repo_path
    embedder = run.index_only_embedder
    # Embedder and model as a pair, so update, serve and the store-format check
    # keep embedding the way this run did.
    save_config_partial(
        repo_path,
        exclude_patterns=run.exclude_patterns if run.exclude_patterns else None,
        commit_limit=run.resolved_commit_limit if run.opts.commit_limit is not None else None,
        embedder=embedder,
        embedding_model=resolve_embedding_model(embedder) if embedder else None,
    )
    # Fingerprint after config writes so the first update doesn't false-positive.
    base_state["config_fingerprint"] = config_fingerprint(repo_path)
    base_state["config_dependency_fingerprints"] = config_dependency_fingerprints(repo_path)
    # Without these stamps the version triggers never fire for keyless installs.
    base_state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION
    base_state["security_scanner_version"] = SECURITY_SCANNER_VERSION
    # Unstamped, the first update would re-render the whole wiki.
    from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint

    base_state["renderer_fingerprint"] = _current_renderer_fingerprint(repo_path)
    # Start the re-score cadence only when a health report was persisted;
    # otherwise the first update's re-score must still run.
    _head_ts = head_commit_ts(repo_path) if getattr(run.result, "health_report", None) else None
    if _head_ts is not None:
        base_state["last_full_rescore_at"] = _head_ts
    save_state(repo_path, base_state, full_index=True)


def write_run_state(run: InitRun) -> None:
    """state.json always; config.yaml for the mode this run ran in."""
    run.scope_embedder = (
        run.embedder_name_resolved if not run.effective_index_only else run.index_only_embedder
    )
    base_state = _base_state(run)
    if run.effective_index_only or run.provider is None:
        _save_index_only_state(run, base_state)

    if not run.effective_index_only and run.provider:
        opts = run.opts
        save_full_state_and_config(
            repo_path=run.repo_path,
            result=run.result,
            provider=run.provider,
            phase_timings=run.phase_timings,
            degraded=run.run_warnings,
            embedder_name_resolved=run.embedder_name_resolved,
            exclude_patterns=run.exclude_patterns,
            commit_limit=opts.commit_limit,
            resolved_commit_limit=run.resolved_commit_limit,
            resolved_reasoning=run.resolved_reasoning,
            max_file_pages=run.max_file_pages,
            include_submodules=opts.include_submodules,
            save_key=opts.save_key,
        )
        _stamp_test_run(run)


def _stamp_test_run(run: InitRun) -> None:
    """Record ``--test-run`` on the model path, which re-reads state from disk.

    Stamped every run, so a whole-repo init clears an earlier sample's mark.
    """
    from repowise.core.index_scope import stamp_index_scope

    state = load_state(run.repo_path)
    stamp_index_scope(state, load_config(run.repo_path), test_run=run.opts.test_run)
    save_state(run.repo_path, state, full_index=True)


def _record_init_outcome(run: InitRun) -> None:
    """Attach the anonymous index-shape outcome to ``command_run``. Best-effort."""
    result = run.result
    try:
        lang_dist = getattr(
            getattr(result, "repo_structure", None), "root_language_distribution", None
        )
        top_language = None
        if isinstance(lang_dist, dict) and lang_dist:
            top_language = max(lang_dist.items(), key=lambda kv: kv[1])[0]

        pages = getattr(result, "generated_pages", None) or []
        if not run.effective_index_only and run.provider is not None:
            docs_mode = "llm"
        else:
            docs_mode = "deterministic" if pages else "none"
        buckets = {
            "file_count": getattr(result, "file_count", 0) or 0,
            "symbol_count": getattr(result, "symbol_count", 0) or 0,
        }
        if pages:
            buckets["deterministic_pages"] = sum(
                1 for p in pages if getattr(p, "provider_name", "") == "template"
            )
    except Exception:
        return
    record_index_outcome(
        index_only=run.effective_index_only,
        provider=run.provider,
        docs_mode=docs_mode,
        pages=pages,
        buckets=buckets,
        fields={
            "run_mode": run.opts.run_mode,
            "top_language": top_language,
            "embedder": run.embedder_name_resolved or None,
        },
    )


def _write_editor_files(run: InitRun) -> list[Path]:
    """Read views, project editor files and the coverage hook; returns the files written.

    Runs after state is saved so the guidance reads the final index_scope.
    """
    repo_path = run.repo_path
    editor_setup = run.opts.editor_setup
    # Before the editor files, which quote Fix first from these views.
    refresh_read_snapshots(repo_path)
    files_written = write_editor_project_files(
        console,
        repo_path,
        options=run.editor_options,
        no_editor_setup=not editor_setup,
    )
    if editor_setup:
        from repowise.cli.commands.augment_cmd.coverage_reingest import sync_repo_hook

        sync_repo_hook(repo_path, console)
    return files_written


def complete_setup(run: InitRun) -> None:
    """Editor files, hook offers, then the completion panel."""
    opts = run.opts
    repo_path = run.repo_path
    files_written = _write_editor_files(run)
    _record_init_outcome(run)

    offer_hook_install(
        console,
        [repo_path],
        flag=opts.hook,
        yes=opts.yes,
        no_editor_setup=not opts.editor_setup,
    )

    offer_distill_rewrite_hook(
        console,
        [repo_path],
        opts.distill_hook,
        yes=opts.yes,
        no_editor_setup=not opts.editor_setup,
    )

    # Last, so the panel reflects what setup did. `interactive` mirrors the
    # offers' gate: when they were skipped the panel names their hooks.
    from repowise.cli.editor_setup import detect_editor_setup_outcome

    setup_outcome = detect_editor_setup_outcome(
        repo_path,
        interactive=(sys.stdin.isatty() and not opts.yes),
        first_index=run.first_index,
        no_editor_setup=not opts.editor_setup,
    )
    show_completion(
        repo_path=repo_path,
        result=run.result,
        start=run.start,
        effective_index_only=run.effective_index_only,
        run_mode=opts.run_mode,
        provider=run.provider,
        setup=setup_outcome,
        files_written=files_written,
    )


def exit_with_gaps(run: InitRun) -> None:
    """Exit degraded when the run left a gap; last, so everything else is kept."""
    from repowise.cli.providers import embed_failure_message

    from ._exit import exit_on_gaps, run_gaps

    result = run.result
    exit_on_gaps(
        run.gaps
        + run_gaps(
            failed_pages=len(getattr(result, "failed_page_ids", None) or []),
            embed_error=embed_failure_message(
                run.scope_embedder, getattr(result, "embed_failed_pages", 0)
            ),
        )
    )

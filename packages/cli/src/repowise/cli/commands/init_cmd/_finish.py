"""After generation: persistence, config and state, editor setup and the completion panel."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli.commands._index_outcome import record_index_outcome, refresh_read_snapshots
from repowise.cli.editor_setup import register_editor_clients, write_editor_project_files
from repowise.cli.helpers import (
    config_fingerprint,
    console,
    get_head_commit,
    head_commit_ts,
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
from repowise.core.docs_mode import docs_mode_state_fields
from repowise.core.generation.styles import DEFAULT_STYLE
from repowise.core.repo_config import config_dependency_fingerprints

from ._interactive import offer_distill_rewrite_hook, offer_hook_install
from ._options import InitRun
from .persistence import (
    apply_git_history_coverage_state,
    git_tier_for_run_mode,
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

    # A bar rather than an indeterminate spinner: full-text indexing walks
    # every generated page one await at a time, so on a few thousand pages
    # this stage runs for minutes. The columns are the index run's, minus the
    # cost one — nothing here spends tokens.
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
        # Indeterminate: persistence has no page-proportional loop to count
        # since the full-text index became a single statement. Announced here
        # so the stage is never silent.
        persist_callback.on_phase_start("persist", None)
        run_async(persist_result(run.result, run.repo_path, persist_callback, recorder.table))
        persist_callback.on_phase_done("persist")
    # Persistence has its own callback, so its warnings need folding into the
    # run record explicitly — the state write is the last chance.
    run.run_warnings.extend(persist_callback.warnings)
    # Read now, not before generation: ingestion, analysis, generation and
    # persistence all write into the one table.
    recorder.table.stop("run")
    run.phase_timings = recorder.timings
    console.print(f"  [{OK}]✓[/] Database updated")


def catch_up_savings(repo_path: Path) -> None:
    """Bank the savings this repository's agents were shown before indexing.

    Keyless, time-budgeted and best-effort, following the session-decision
    stage that runs here for the same reason: the history is already on disk,
    and a first index that ignores it shows an empty savings page for a
    repository that has been saving tokens for months.

    Not in ``repowise update``: update runs on every commit, and this reads a
    corpus bounded by how much the user has worked rather than by the repo.
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
    # Onboarding: default True is omitted to keep config files tidy — only the
    # override is recorded.
    if not opts.onboarding:
        save_config_partial(repo_path, enable_onboarding=False)

    # The file-page decision, so `repowise update --full` and `repowise
    # generate` keep the wiki the size that was asked for. Recorded whether or
    # not a model wrote the pages. A refusal (0) is recorded like any other
    # answer; only an unset value stays unwritten (save_config_partial drops
    # None, and 0 is not None).
    if run.max_file_pages is not None and run.max_file_pages != run.config.get("max_file_pages"):
        save_config_partial(repo_path, max_file_pages=run.max_file_pages)

    # Wiki style, written before any config_fingerprint is computed so the
    # first update doesn't false-positive on a config change. A template wiki
    # records neither style nor language: it has neither, and recording them
    # would make `restyle` believe the pages are already in that style and
    # refuse the very run that would put them there.
    if opts.wiki_style != DEFAULT_STYLE and not run.effective_index_only:
        save_config_partial(repo_path, wiki_style=opts.wiki_style)

    # Output language, only when it differs from what config.yaml holds.
    if run.language != run.config.get("language", "en") and not run.effective_index_only:
        save_config_partial(repo_path, language=run.language)

    if opts.commit_limit is not None:
        save_config_partial(repo_path, commit_limit=run.resolved_commit_limit)


def register_clients(run: InitRun) -> None:
    """Global client registration, then the workspace's distill verdict.

    One flag, one meaning: --no-editor-setup suppresses the project-local
    writes as well as the global registration.
    """
    register_editor_clients(console, run.repo_path, no_editor_setup=not run.opts.editor_setup)

    # Inherit the workspace's distill rewrite-hook verdict now, before the
    # config fingerprint is computed. `repowise update` runs the same backfill
    # at its start; if init leaves the verdict unwritten, the first update
    # writes it, sees a fingerprint mismatch, and silently replaces the
    # incremental path with a full health re-score of every file.
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    inherit_workspace_distill_verdict(run.repo_path)


def _docs_mode(run: InitRun) -> str:
    """A full run wrote its pages with a model; index-only (or a declined cost
    gate) fell back to templates. Fast mode is the one path with no pages."""
    if run.opts.run_mode == "fast":
        return "none"
    if not run.effective_index_only and run.provider is not None:
        return "llm"
    return "deterministic"


def _stamp_scope(run: InitRun, base_state: dict[str, Any], docs_mode: str) -> None:
    from repowise.cli.providers import semantic_search_status
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
        search={
            "full_text": "available" if result.generated_pages else "unavailable",
            "semantic": (
                semantic_search_status(scope_embedder, getattr(result, "embed_failed_pages", 0))
                if result.generated_pages
                else "unavailable"
            ),
            "next_command": "repowise reindex" if result.generated_pages else None,
        },
        upgrade={
            "status": "pending" if docs_mode != "llm" else "not_applicable",
            "retryable": docs_mode != "llm",
            "completed_stages": [],
            "next_stage": "git_backfill" if run_mode == "fast" else "generation",
        },
    )


def _base_state(run: InitRun) -> dict[str, Any]:
    """state.json for this run, before the mode-specific stamps.

    Even in index-only mode ``last_sync_commit`` is persisted so that a later
    ``repowise update`` (e.g. fired by the post-commit hook) has a baseline to
    diff against.
    """
    repo_path = run.repo_path
    result = run.result
    run_mode = run.opts.run_mode
    head = get_head_commit(repo_path)
    base_state = load_state(repo_path)
    base_state["last_sync_commit"] = head
    docs_mode = _docs_mode(run)
    base_state.update(docs_mode_state_fields(docs_mode))
    # An index-only run produces pages too, so it records its own count or
    # `status` reports a wiki of zero pages next to a docs mode that says
    # there is one.
    base_state["total_pages"] = len(getattr(result, "generated_pages", None) or [])
    # The git tier this run indexed, so a later --resume continues the same
    # tier instead of silently upgrading ESSENTIAL → FULL (issue #341).
    base_state["run_mode"] = run_mode
    base_state["git_tier"] = git_tier_for_run_mode(run_mode)
    apply_git_history_coverage_state(base_state, result)
    _stamp_scope(run, base_state, docs_mode)
    # Whether submodules were indexed, so `repowise update` rebuilds the graph
    # with the same boundary semantics (missing → False for old state files).
    base_state["include_submodules"] = run.opts.include_submodules
    if run.phase_timings:
        base_state["phase_timings"] = run.phase_timings
    # Cleared before it is set: a fixed re-run must not inherit its
    # predecessor's degradation report.
    base_state.pop("degraded", None)
    if run.run_warnings:
        base_state["degraded"] = run.run_warnings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        base_state["knowledge_graph"] = build_kg_state(kg)
        save_knowledge_graph_json(repo_path, kg)
    return base_state


def _save_index_only_state(run: InitRun, base_state: dict[str, Any]) -> None:
    """Config and state for a run with no model (save_config() never ran)."""
    from repowise.cli.providers.embedders import resolve_embedding_model

    repo_path = run.repo_path
    embedder = run.index_only_embedder
    # The embedder rides along: without it `repowise update` would re-resolve
    # from the environment and could start embedding, at a different width, a
    # store this run built with the mock. ``embedding_model`` rides with it:
    # `serve` pins the model from it, and the store-format upgrade check reads
    # it to notice an embedder change.
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
    # Index-only is the keyless default, so this is where most installs get
    # their analyzer and scanner stamps. Without them the version triggers
    # read absent-as-unchanged and never fire.
    base_state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION
    base_state["security_scanner_version"] = SECURITY_SCANNER_VERSION
    # The pages were just rendered by this release's structural renderer.
    # Unstamped, the first update would re-render the whole wiki.
    from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint

    base_state["renderer_fingerprint"] = _current_renderer_fingerprint(repo_path)
    # This run just scored every file, so the periodic re-score cadence starts
    # now. Only when there really is a report: the health phase swallows its
    # own failures and returns None, nothing is persisted for a None report,
    # and stamping there would suppress the first update's re-score. None (no
    # git) is left unstamped too.
    _head_ts = head_commit_ts(repo_path) if getattr(run.result, "health_report", None) else None
    if _head_ts is not None:
        base_state["last_full_rescore_at"] = _head_ts
    # Index-only still renders the full concept tree, so the store has the
    # current capability: stamp the terminal version.
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


def _record_init_outcome(run: InitRun) -> None:
    """Attach an anonymous shape-of-the-index outcome to the ``command_run`` event.

    File and symbol buckets, run mode, top language and embedder on top of the
    shared index shape. Best-effort: never let telemetry break the command.
    """
    result = run.result
    try:
        lang_dist = getattr(
            getattr(result, "repo_structure", None), "root_language_distribution", None
        )
        top_language = None
        if isinstance(lang_dist, dict) and lang_dist:
            top_language = max(lang_dist.items(), key=lambda kv: kv[1])[0]

        # docs_mode carries the same three values as the state field of that
        # name, so "template wiki" and "no wiki" stay distinct.
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
    """Read views, project-local editor files and the coverage hook.

    The paths come back so the completion panel can name what landed in the
    working tree. Rendered after state persistence so generated guidance reads
    the same canonical index_scope that status/API/MCP expose.
    """
    repo_path = run.repo_path
    editor_setup = run.opts.editor_setup
    # Every store is final: store the views ``repowise next`` and the
    # dashboard read, before the editor files quote Fix first from them.
    refresh_read_snapshots(repo_path)
    files_written = write_editor_project_files(
        console,
        repo_path,
        options=run.editor_options,
        no_editor_setup=not editor_setup,
    )
    if editor_setup:
        # The index may carry a coverage ingest now; see ``sync_repo_hook``.
        from repowise.cli.commands.augment_cmd.coverage_reingest import sync_repo_hook

        sync_repo_hook(repo_path, console)
    return files_written


def complete_setup(run: InitRun) -> None:
    """Editor files, hook offers, then the completion panel."""
    opts = run.opts
    repo_path = run.repo_path
    files_written = _write_editor_files(run)
    _record_init_outcome(run)

    # Post-commit auto-sync hook (both index-only and full modes).
    offer_hook_install(
        console,
        [repo_path],
        flag=opts.hook,
        yes=opts.yes,
        no_editor_setup=not opts.editor_setup,
    )

    # Opt-in distill command-rewrite hook for Claude Code. The workspace flow
    # runs its own offer across all selected repos inside _workspace_init.
    offer_distill_rewrite_hook(
        console,
        [repo_path],
        opts.distill_hook,
        yes=opts.yes,
        no_editor_setup=not opts.editor_setup,
    )

    # Completion panel last, so it reflects what setup actually did.
    # `interactive` mirrors the offers' own gate: when False they were
    # skipped, so the panel is the only place their hooks surface.
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


def raise_on_embed_failure(run: InitRun) -> None:
    """Raised last, so pages, state and full-text search are all kept.

    Exiting 0 here is what let a scripted run record a healthy semantic index
    that held no vectors.
    """
    from repowise.cli.providers import embed_failure_message

    embed_error = embed_failure_message(
        run.scope_embedder, getattr(run.result, "embed_failed_pages", 0)
    )
    if embed_error:
        raise click.ClickException(embed_error)

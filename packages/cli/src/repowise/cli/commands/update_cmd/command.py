"""``repowise update`` — incremental wiki regeneration for changed files.

This module holds the Click command + single-repo orchestration. The standalone
helpers it relies on live in sibling modules: mode resolution (:mod:`.mode`),
core delegators (:mod:`.incremental`), persistence + re-score
(:mod:`.persistence`), the workspace flow (:mod:`.workspace`), and report
rendering (:mod:`.reporting`).
"""

from __future__ import annotations

import contextlib
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import click
import structlog

from repowise.cli._setup import configure_cli_logging
from repowise.cli.commands._index_outcome import record_index_outcome, refresh_read_snapshots
from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import (
    clear_update_queued,
    console,
    consume_update_pending,
    ensure_repowise_dir,
    find_workspace_root,
    get_head_commit,
    load_config,
    load_state,
    release_update_lock,
    resolve_command_target,
    resolve_provider_or_prompt,
    resolve_reasoning,
    rotate_update_log_if_needed,
    run_async,
    save_state,
    silence_logs_for_machine_output_until_close,
    try_acquire_update_lock,
    write_update_pending,
)
from repowise.cli.ui.mode_names import scope_line
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode
from repowise.core.ingestion.git_indexer import (
    GIT_HISTORY_BACKFILL_REWALK,
    GIT_HISTORY_VERSION,
    GIT_HISTORY_VERSION_KEY,
    git_history_stale,
)
from repowise.core.pipeline import PhaseTimings, timed
from repowise.core.reasoning import REASONING_MODES

from .config_changes import classify_config_changes
from .decision_passes import _run_decision_evolution as _run_decision_evolution
from .decision_passes import run_decision_passes
from .incremental import (
    _build_update_vector_store,
    _load_stored_function_mod_p80,
    _load_stored_git_meta,
    _load_stored_performance_callers,
    _rebuild_graph_and_git,
    _refresh_knowledge_graph,
    _run_doc_drift_partial,
    _run_partial_analysis,
)
from .mode import _resolve_index_only_mode
from .options import DocsPass, UpdateOptions, UpdateRun
from .page_generation import _generation_warning_handler as _generation_warning_handler
from .page_generation import generate_pages, render_stale_structure
from .persistence import (
    _persist_index_only_update,
    _repair_module_attribution,
    _run_full_health_rescore,
    heal_commit_offsets,
    health_analyzer_changed,
    parser_changed,
    rescan_security_if_changed,
    security_scanner_changed,
    stamp_file_page_scope,
    stamp_head_commit,
)
from .reporting import (
    JsonProgressEmitter,
    _render_update_report,
    render_changed_files,
    render_header,
    show_full_completion,
)
from .workspace import _workspace_update

log = structlog.get_logger(__name__)

def _fail_on_embed_failure(
    repo_path: Path, embedder: str | None, embed_failed_pages: int, emitter: Any
) -> None:
    """Exit non-zero, and say semantic search is unavailable, after a failed embed.

    Called once the run's pages and state are saved: full-text search is
    fine, so only the search leg and the exit status change. A zero exit here
    let a scripted run, or the post-commit log, read a failed write as a
    healthy index.
    """
    from repowise.cli.providers import embed_failure_message, semantic_search_command
    from repowise.core.index_scope import stamp_index_scope

    message = embed_failure_message(embedder, embed_failed_pages)
    if message is None:
        return
    state = load_state(repo_path)
    stamp_index_scope(
        state,
        load_config(repo_path),
        search={
            "semantic": "unavailable",
            "next_command": semantic_search_command(repo_path, embedder),
        },
    )
    save_state(repo_path, state)
    if emitter is not None:
        emitter.error(message)
    raise reasoned_error(message, reason="embed_failed")


def _docs_provider_prompt_allowed(emitter: Any) -> bool:
    """Whether a docs run may block on an interactive provider prompt.

    Requires a real terminal on *both* ends: a terminal stdout (so the table
    renders) and a tty stdin (so the answer can actually be read). The stdin
    check is what keeps a background post-commit hook or CI run from hanging
    even when it inherits a ``FORCE_COLOR`` that makes ``console.is_terminal``
    report True. ``--progress json`` (emitter set) is always non-interactive.
    """
    return console.is_terminal and sys.stdin.isatty() and emitter is None


def _record_update_outcome(
    *,
    index_only: bool,
    changed_count: int,
    provider: Any = None,
    generated_pages: list | None = None,
    docs_mode: str | None = None,
) -> None:
    """Attach an anonymous update-shape outcome to the ``command_run`` event.

    Changed-files bucket on top of the shared index shape.
    """
    # docs_mode carries the same three values as the state field of that
    # name; it was a bool until an index-only update started re-rendering
    # template pages, which that shape could not express. It comes from the
    # repo's persisted mode, not from this run's page count: a commit that
    # touched no source file re-renders nothing, and that is not the same
    # as the repo having no wiki.
    if docs_mode is None:
        docs_mode = "llm" if (not index_only and provider is not None) else "none"
    record_index_outcome(
        index_only=index_only,
        provider=provider,
        docs_mode=docs_mode,
        pages=generated_pages,
        buckets={"changed_files": changed_count},
    )


def _finish_outcome(
    repo_path: Any,
    agents_md: bool | None,
    degraded: list[str] | None = None,
    *,
    write: bool = True,
    timings: PhaseTimings | None = None,
) -> None:
    """Every update outcome ends here: the stored read views, then the editor
    files that quote them. ``write=False`` (a dry run) skips the views."""
    if write:
        with timed(timings, "read_snapshots"):
            refresh_read_snapshots(repo_path)
    with timed(timings, "editor_files"):
        _refresh_editor_stamp(repo_path, agents_md, degraded)


def _refresh_editor_stamp(
    repo_path: Any, agents_md: bool | None, degraded: list[str] | None = None
) -> None:
    """Re-stamp managed editor files (CLAUDE.md / AGENTS.md), best-effort.

    Runs on every update outcome — including the "already up to date" and
    "no changed files" fast paths, matching the workspace flow — so the
    "Last indexed" stamp always reflects the latest successful sync check
    instead of freezing at the last content-changing run.
    """
    try:
        from repowise.cli.editor_integrations.defaults import get_default_project_file_overrides
        from repowise.cli.editor_setup import EditorSetupOptions, refresh_editor_project_files

        options = None
        if agents_md is not None:
            options = EditorSetupOptions(
                project_file_overrides=get_default_project_file_overrides(agents_md=agents_md),
            )
        refresh_editor_project_files(console, repo_path, options=options)
    except Exception as exc:
        # Editor project-file refresh must never fail the update command,
        # but a stale CLAUDE.md stamp is worth an honest mention.
        if degraded is not None:
            degraded.append(f"Editor file refresh: {exc}")
    # This run may have stored coverage (or the flag changed): keep the hook in step.
    from repowise.cli.commands.augment_cmd.coverage_reingest import sync_repo_hook

    sync_repo_hook(repo_path, console)


def _surface_release_news(*, written_by: str | None) -> None:
    """Show a post-upgrade "what's new" panel and a cached PyPI advisory.

    Best-effort and interactive-only (the caller gates on a terminal). The
    what's-new panel appears only when the store was written by an older repowise
    than the one running now.
    """
    from repowise.cli import __version__
    from repowise.cli.update_check import get_cli_update_check_cached
    from repowise.cli.whats_new import (
        load_changelog_entries,
        render_update_advisory,
        render_whats_new,
    )

    if written_by and written_by != __version__:
        entries = load_changelog_entries()
        render_whats_new(
            console,
            entries,
            since_version=written_by,
            up_to_version=__version__,
            title=f"Upgraded to v{__version__} - what's new",
        )
    render_update_advisory(console, get_cli_update_check_cached())


def _surface_reindex_recommendation(repo_path, verdict, *, emitter: Any, dry_run: bool) -> None:
    """Show the once-per-store reindex recommendation for *verdict*, if any.

    Read-only except for the shown-notice ledger, so it is safe to call on the
    no-op ("already up to date") path as well as the main assessment path. It
    never runs the verdict's auto actions — those stay behind the single-flight
    lock in the main path. Interactive-terminal only, so a background post-commit
    update never burns the one-shot into a log the user never reads; `doctor`
    reports the recommendation on demand regardless.
    """
    from repowise.cli.upgrade import (
        record_reindex_notice_shown,
        reindex_notice_already_shown,
    )

    if not (verdict.reindex_recommended and verdict.reindex_command):
        return
    if not (console.is_terminal and emitter is None):
        return
    if reindex_notice_already_shown(repo_path, verdict):
        return
    console.print(f"[yellow]Reindex recommended:[/yellow] {verdict.reindex_command}")
    if verdict.user_notice:
        console.print(f"[dim]{verdict.user_notice}[/dim]")
    if not dry_run:
        record_reindex_notice_shown(repo_path, verdict)


@click.command("update")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--since", default=None, help="Base git ref to diff from (overrides state).")
@click.option("--concurrency", type=int, default=10, help="Max concurrent LLM calls.")
@click.option(
    "--reasoning",
    type=click.Choice(REASONING_MODES),
    default=None,
    help=(
        "Reasoning mode for supported providers: auto, off/none, minimal, "
        "low, medium, high, xhigh, or max."
    ),
)
@click.option(
    "--cascade-budget",
    type=int,
    default=None,
    help="Max pages to regenerate (auto-scaled if unset).",
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show affected pages without regenerating."
)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (update all stale repos in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Update only this repo alias within the workspace (implies --workspace).",
)
@click.option(
    "--index-only",
    is_flag=True,
    default=False,
    help=(
        "Skip LLM page regeneration. Re-parses files, rebuilds the dependency "
        "graph, and refreshes git/dead-code artifacts only. Useful for tight "
        "iteration on extractors / resolvers without spending tokens."
    ),
)
@click.option(
    "--working-tree",
    "include_working_tree",
    is_flag=True,
    default=False,
    help=(
        "Also index uncommitted work (staged, unstaged and untracked files) on "
        "top of the commits since the last sync. In a workspace, a repo with "
        "uncommitted changes counts as stale and is updated the same way."
    ),
)
@click.option(
    "--docs/--no-docs",
    "docs_flag",
    default=None,
    help=(
        "Override the persisted init mode for this run. --no-docs is "
        "equivalent to --index-only; --docs forces LLM page regeneration "
        "even when the repo was indexed without docs. Without either flag, "
        "the value of `docs_mode` from .repowise/state.json is used "
        "(set during `repowise init`)."
    ),
)
@click.option(
    "--yes",
    "-y",
    "yes",
    is_flag=True,
    default=False,
    help="Skip the cost confirmation on `--full`.",
)
@click.option(
    "--full",
    "full",
    is_flag=True,
    default=False,
    help=(
        "Take an Index only (`--mode fast`) repo to a full index: backfill the "
        "full git history and build the documentation. Works without a key: with "
        "no model configured the documentation is built from code (no model calls, "
        "no spend); with one, the model writes it after a cost preview. Reuses the "
        "persisted graph and resumes from the last completed stage. One repo at a "
        "time (in a workspace, pass --repo <alias>)."
    ),
)
@click.option(
    "--no-cost-tracking",
    is_flag=True,
    default=False,
    help=(
        "Skip DB-backed LLM cost tracking for this run. Avoids opening a second "
        "engine on wiki.db, so cost writes can never contend with the "
        "generation writer. The live cost readout still works; only historical "
        "`repowise costs` rows are skipped. Also settable via the "
        "REPOWISE_NO_COST_TRACKING env var."
    ),
)
@click.option(
    "--agents/--no-agents",
    "agents_md",
    default=None,
    help="Generate managed AGENTS.md after update (default: config or enabled).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help=(
        "Show the full changed-file list and per-phase internals (adaptive "
        "cascade budget, decision-marker/evolution counts, best-effort skip "
        "warnings, and the detailed generation report), plus debug logs. "
        "Without it, debug/info logging is suppressed so the progress bar is "
        "the only output."
    ),
)
@click.option(
    "--progress",
    type=click.Choice(["rich", "json"]),
    default="rich",
    help=(
        "Progress output. 'rich' (default) shows the interactive owl-spinner "
        "progress bar. 'json' silences it and prints one newline-delimited "
        "JSON event per line to stdout instead, for driving this from "
        "another process."
    ),
)
def update_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    since: str | None,
    reasoning: str | None,
    cascade_budget: int | None,
    dry_run: bool,
    workspace: bool,
    no_workspace: bool,
    repo_alias: str | None,
    index_only: bool = False,
    docs_flag: bool | None = None,
    full: bool = False,
    yes: bool = False,
    agents_md: bool | None = None,
    concurrency: int = 10,
    no_cost_tracking: bool = False,
    verbose: bool = False,
    progress: str = "rich",
    include_working_tree: bool = False,
) -> None:
    """Incrementally update the index and pages for files changed since the last sync.

    Auto-detects workspace mode when invoked from a workspace root or when a
    workspace exists upstream of the working directory. Use --no-workspace to
    force single-repo mode and --workspace to force workspace mode.
    """
    # Click ignores the return value, so don't forward run_update's outcome
    # here (keeps this command's ``-> None`` annotation honest).
    run_update(
        path=path,
        provider_name=provider_name,
        model=model,
        since=since,
        reasoning=reasoning,
        cascade_budget=cascade_budget,
        dry_run=dry_run,
        workspace=workspace,
        no_workspace=no_workspace,
        repo_alias=repo_alias,
        index_only=index_only,
        docs_flag=docs_flag,
        full=full,
        yes=yes,
        agents_md=agents_md,
        concurrency=concurrency,
        no_cost_tracking=no_cost_tracking,
        verbose=verbose,
        progress=progress,
        include_working_tree=include_working_tree,
    )


class UpdateOutcome(StrEnum):
    """What a single-repo :func:`run_update` actually did.

    The workspace docs loop used to count every non-raising ``run_update``
    return as a regeneration, so a run that bailed on the single-flight lock
    (another update already in flight) still printed "N with docs
    regenerated". Returning the real outcome lets the caller report honestly.
    """

    REGENERATED = "regenerated"  # pages/index actually rewritten
    DEFERRED = "deferred"  # another update held the single-flight lock; queued
    NOOP = "noop"  # already current / no relevant changes to regenerate
    DRY_RUN = "dry_run"  # dry run, nothing written


def _render_full_upgrade_dry_run(
    repo_path: Path,
    *,
    provider_name: str | None,
    model: str | None,
) -> None:
    """Describe a full upgrade without resolving providers or touching the store."""
    state = load_state(repo_path)
    config = load_config(repo_path)
    from repowise.core.index_scope import resolve_index_scope

    scope = resolve_index_scope(state, config)

    planned_provider = (
        provider_name or config.get("provider") or state.get("provider") or "auto-detect"
    )
    planned_model = model or config.get("model") or state.get("model") or "provider default"
    current_git_tier = scope["git_tier"].upper()
    recorded_pages = state.get("total_pages")
    page_label = f"{recorded_pages:,}" if isinstance(recorded_pages, int) else "unknown"

    console.print("[yellow]Dry run: full upgrade plan:[/yellow]")
    console.print(f"  Provider: [cyan]{planned_provider}[/cyan] / [cyan]{planned_model}[/cyan]")
    console.print(f"  Git tier: [cyan]{current_git_tier}[/cyan] -> [cyan]FULL[/cyan]")
    console.print(f"  Pages currently recorded: [cyan]{page_label}[/cyan]")
    console.print(
        f"  Persisted scope: [cyan]{scope_line(scope)}[/cyan]"
    )
    console.print(
        "  Documentation for the whole repository: written by the model after a cost "
        "preview, or built from code when no model is configured. Completed stages "
        "are resumable. No changes made."
    )


def _renderer_inputs(repo_path):
    """The (language, style fingerprint, style template dir) a file page uses.

    Read from the persisted config so the fingerprint computed at the "up to
    date" gate matches the one the pages actually store. A wrong triple here
    would make every page look stale on every run (or none ever), so all three
    come from the same place generation reads them. The template dir is a
    custom style's own ``templates/`` (``None`` for a built-in style), tried
    ahead of the shipped template exactly as the generator's Jinja loader does.
    """
    from repowise.cli.helpers import load_config
    from repowise.core.generation.styles import resolve_style

    cfg = load_config(repo_path)
    style = resolve_style(cfg.get("wiki_style", "comprehensive"), repo_path=repo_path)
    return cfg.get("language", "en"), style.fingerprint, style.template_dir


def _head_commit_ts(repo_path) -> float | None:
    """Committer timestamp of the repo's HEAD, or None when git is unavailable.

    Lives in ``cli.helpers`` so ``init`` stamps the re-score gate in the units
    this path reads back. Kept as a thin module-level name because this module
    is where the gate is evaluated, and a caller reading ``_head_commit_ts``
    here should not have to know it resolves elsewhere.
    """
    from repowise.cli.helpers import head_commit_ts

    return head_commit_ts(repo_path)


def _current_renderer_fingerprint(repo_path) -> str:
    """This release's fingerprint for the structural renderers, or '' on failure.

    Covers every template with no model path behind it, because this value is
    what decides whether a repository with no new commits is "already up to
    date". Fingerprinting only the file page meant a release that improved the
    spotlight template alone returned before doing anything, and the change
    never reached an existing wiki.

    Cheap: reads the templates and a few constants, no parse. An empty result
    disables the renderer-staleness gate for this run rather than risking a
    spurious full re-render, which is the safe direction.
    """
    try:
        from repowise.core.generation.page_generator.structural import (
            FILE_PAGE_TEMPLATE,
            SYMBOL_SPOTLIGHT_TEMPLATE,
            structural_fingerprint,
        )

        language, style_fp, template_dir = _renderer_inputs(repo_path)
        return ":".join(
            structural_fingerprint(
                template,
                language=language,
                style_fingerprint=style_fp,
                template_dir=template_dir,
            )
            for template in (FILE_PAGE_TEMPLATE, SYMBOL_SPOTLIGHT_TEMPLATE)
        )
    except Exception as exc:
        log.debug("update.renderer_fingerprint_failed", error=str(exc))
        return ""


def _stale_renderer_paths(repo_path, parsed_files) -> list[str]:
    """File paths whose stored pages predate the current structural renderers.

    Covers file pages and symbol spotlights. Both sweeps return file paths, so
    the union is the set of files to re-parse; regenerating a file re-renders
    every page derived from it, including its spotlights.

    Never raises: a failure here means the run behaves exactly as it did
    before the fingerprint existed, which is the safe direction. The style and
    language have to match what generation will actually use, or every page
    would look stale on every run.
    """
    try:
        from repowise.core.generation.page_generator.structural import (
            stale_file_page_paths,
            stale_spotlight_paths,
        )

        from .deterministic import load_file_page_render_keys, load_spotlight_render_keys

        parsed_files = list(parsed_files)
        language, style_fp, template_dir = _renderer_inputs(repo_path)
        kwargs = {
            "language": language,
            "style_fingerprint": style_fp,
            "template_dir": template_dir,
        }

        stale: list[str] = []
        stored_pages = load_file_page_render_keys(repo_path)
        if stored_pages:
            stale.extend(stale_file_page_paths(stored_pages, parsed_files, **kwargs))
        stored_spotlights = load_spotlight_render_keys(repo_path)
        if stored_spotlights:
            stale.extend(stale_spotlight_paths(stored_spotlights, parsed_files, **kwargs))
        return list(dict.fromkeys(stale))
    except Exception as exc:
        log.debug("update.stale_renderer_check_failed", error=str(exc))
        return []


def _start_run(opts: UpdateOptions) -> UpdateRun:
    """Start the run clock and pick the progress output (rich or JSON events)."""
    # ``run`` is the denominator of every stage row; stages nest and overlap.
    run = UpdateRun(start=time.monotonic(), timings=PhaseTimings())
    run.timings.start("run")

    if opts.progress == "json":
        # stdout carries only JSON events; the shared console moves to stderr
        # until the click context closes.
        silence_logs_for_machine_output_until_close()
        console.file = sys.stderr
        # None, not a captured file: the console then follows sys.stdout again.
        click.get_current_context().call_on_close(lambda: setattr(console, "file", None))
        run.emitter = JsonProgressEmitter()
        run.emitter.start(repo=str(opts.path or "."), since=opts.since)
    else:
        # Quiet library logs so they do not interleave with the progress bar.
        configure_cli_logging(verbose=opts.verbose)
    return run


def _emit_done(
    run: UpdateRun,
    outcome: UpdateOutcome,
    *,
    pages_generated: int = 0,
    cost_usd: float = 0.0,
    duration_s: float | None = None,
    degraded: list[str] | None = None,
) -> UpdateOutcome:
    """Send the JSON ``done`` event (when one is wanted) and hand back *outcome*."""
    if run.emitter is not None:
        extra = {} if degraded is None else {"degraded": degraded}
        run.emitter.done(
            ok=True,
            pages_generated=pages_generated,
            cost_usd=cost_usd,
            duration_s=time.monotonic() - run.start if duration_s is None else duration_s,
            **extra,
            outcome=outcome.value,
        )
    return outcome


@contextlib.contextmanager
def _errors_reported(run: UpdateRun):
    """Report an escaping error as a JSON ``error`` event, then re-raise it."""
    try:
        yield
    except Exception as exc:
        if run.emitter is not None:
            run.emitter.error(str(exc))
        raise


def _stage(run: UpdateRun, name: str) -> None:
    if run.emitter is not None:
        run.emitter.stage(name)


def _resolve_target(opts: UpdateOptions):
    """Resolve the target up front (single repo or workspace) and announce it."""
    target = resolve_command_target(
        path=opts.path,
        workspace_flag=opts.workspace,
        no_workspace_flag=opts.no_workspace,
        repo_alias=opts.repo_alias,
    )
    target.notice(console, command="update")
    return target


def _update_workspace_target(opts: UpdateOptions, run: UpdateRun, target) -> UpdateOutcome:
    if opts.full and opts.repo_alias:
        entry = target.ws_config.get_repo(opts.repo_alias)
        run.repo_path = (target.ws_root / entry.path).resolve()
        return _full_upgrade(opts, run)
    if opts.full:
        raise reasoned_error(
            "--full upgrades one repo at a time. Run it inside that repo, or pass "
            "--repo <alias>.",
            reason="full_needs_one_repo",
        )
    with _errors_reported(run):
        _workspace_update(
            target,
            dry_run=opts.dry_run,
            agents_md=opts.agents_md,
            verbose=opts.verbose,
            docs_flag=opts.docs_flag,
            index_only=opts.index_only,
            since=opts.since,
            provider_name=opts.provider_name,
            model=opts.model,
            reasoning=opts.reasoning,
            cascade_budget=opts.cascade_budget,
            concurrency=opts.concurrency,
            no_cost_tracking=opts.no_cost_tracking,
            progress=opts.progress,
            include_working_tree=opts.include_working_tree,
        )
    return _emit_done(run, UpdateOutcome.REGENERATED)


def _full_upgrade(opts: UpdateOptions, run: UpdateRun) -> UpdateOutcome:
    """``--full``: upgrade the index in place from the persisted graph."""
    if opts.dry_run:
        _render_full_upgrade_dry_run(
            run.repo_path,
            provider_name=opts.provider_name,
            model=opts.model,
        )
        return _emit_done(run, UpdateOutcome.DRY_RUN)

    from repowise.cli.commands.upgrade_flow import upgrade_to_full

    _stage(run, "full_upgrade")
    with _errors_reported(run):
        upgrade_to_full(
            run.repo_path,
            provider_name=opts.provider_name,
            model=opts.model,
            reasoning=opts.reasoning,
            concurrency=opts.concurrency,
            yes=opts.yes,
        )
    return _emit_done(run, UpdateOutcome.REGENERATED)


def _prepare_repo(repo_path: Path) -> None:
    """Seed an unindexed worktree, create ``.repowise/``, inherit workspace verdicts."""
    # A fresh linked worktree seeds from its base checkout (best-effort).
    if not (repo_path / ".repowise" / "state.json").exists():
        from repowise.cli.worktree import (
            base_is_seedable,
            detect_worktree_base,
            seed_index_from_base,
        )

        wt_base = detect_worktree_base(repo_path)
        if wt_base is not None and base_is_seedable(wt_base):
            console.print(
                f"[dim]\\[worktree][/dim] Unindexed linked worktree of {wt_base}; "
                f"seeding its index."
            )
            seed_index_from_base(
                root=repo_path, repo_paths=[repo_path], seed_base=wt_base, require_ancestor=False
            )

    ensure_repowise_dir(repo_path)

    # A workspace member inherits the workspace's distill verdict (best-effort).
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    inherit_workspace_distill_verdict(repo_path)


def _load_sync_base(opts: UpdateOptions, run: UpdateRun) -> None:
    """Load state and settle the mode, the base ref and HEAD for this run."""
    repo_path = run.repo_path
    # The post-commit hook appends to .update.log on every run; cap it here.
    rotate_update_log_if_needed(repo_path)

    # Saved API keys; existing env vars win.
    from repowise.cli.ui import load_dotenv

    load_dotenv(repo_path)
    run.state = state = load_state(repo_path)
    # One-time migration: drop informational notices saved into state["degraded"]
    # by pre-v0.56.0 init runs (#4224, #3272).
    from repowise.core.index_scope import clean_legacy_degraded_state

    clean_legacy_degraded_state(state)
    run.index_only = _resolve_index_only_mode(
        index_only=opts.index_only, docs_flag=opts.docs_flag, state=state
    )
    # ``or``, not a ``get`` default: ``last_docs_commit`` can be an explicit null.
    run.base_ref = opts.since or (
        state.get("last_sync_commit")
        if run.index_only
        else state.get("last_docs_commit") or state.get("last_sync_commit")
    )
    run.head = get_head_commit(repo_path)

    if run.base_ref is None:
        _raise_no_sync_base(run)

    # Re-cover a range an earlier update failed to persist. Index-only only: the
    # repair marker is written against ``last_sync_commit``, and --since wins.
    if opts.since is None and run.index_only:
        _widen_to_pending_repair(run)


def _raise_no_sync_base(run: UpdateRun) -> None:
    hint = ""
    upstream_ws = find_workspace_root(run.repo_path)
    if upstream_ws is not None:
        hint = (
            f"\nA workspace was detected at {upstream_ws}. "
            "Did you mean: repowise update --workspace?"
        )
    message = (
        f"No previous sync found for {run.repo_path}. "
        "Run 'repowise init' there first, or pass --since <ref>." + hint
    )
    if run.emitter is not None:
        run.emitter.error(message)
    # An empty state means the repo was never indexed; a state without a sync
    # commit means it was, but no sync completed.
    raise reasoned_error(message, reason="no_sync_base" if run.state else "no_index")


def _widen_to_pending_repair(run: UpdateRun) -> None:
    from .persistence import resolve_repair_base

    repaired_ref, give_up = resolve_repair_base(run.repo_path, run.state, run.base_ref, run.head)
    if give_up is not None:
        console.print(
            f"[yellow]Dropping the pending repair of an earlier update: {give_up}. "
            "Run [bold]repowise init --force[/bold] to rebuild the index.[/yellow]"
        )
        # Dropped in memory only: every save_state past the lock builds on this dict.
        run.state.pop("pending_repair", None)
    elif repaired_ref != run.base_ref:
        console.print(
            f"[dim]Re-covering commits an earlier update did not finish "
            f"persisting (from {repaired_ref[:8]}).[/dim]"
        )
        run.base_ref = repaired_ref


def _check_config_and_renderer(run: UpdateRun) -> None:
    """Compare the stored config and renderer fingerprints with the live ones."""
    repo_path = run.repo_path
    # The tolerant loader: a malformed file warns and falls back to defaults.
    run.cfg = load_config(repo_path)
    run.config = classify_config_changes(repo_path, run.state, run.cfg)

    # A renderer upgrade retires the "up to date" shortcut for one run so stale
    # structural pages re-render. A missing stamp counts as a change; an empty
    # current fingerprint (it failed) disables the gate.
    run.prev_renderer_fp = run.state.get("renderer_fingerprint")
    run.curr_renderer_fp = _current_renderer_fingerprint(repo_path)
    # A model wiki waits for a full update, the run that can re-render it.
    docs_mode = resolve_docs_mode(run.state)
    run.renderer_changed = (
        bool(run.curr_renderer_fp)
        and run.prev_renderer_fp != run.curr_renderer_fp
        and docs_mode != "none"
        and not (run.index_only and docs_mode == "llm")
    )


def _collect_working_tree(opts: UpdateOptions, run: UpdateRun) -> None:
    """Uncommitted work, when asked for; read before the "up to date" gate."""
    from repowise.core.ingestion import ChangeDetector

    run.detector = detector = ChangeDetector(run.repo_path)
    if not opts.include_working_tree:
        return
    run.working_tree_diffs = detector.get_working_tree_changes()
    dirty_paths = {fd.path for fd in run.working_tree_diffs}
    # Paths the last working-tree run indexed that now match HEAD again.
    run.working_tree_diffs += detector.stale_working_tree_diffs(
        run.state.get("working_tree_paths") or [], dirty_paths
    )
    # On ``state`` so every later save_state, early exits included, persists it.
    run.state["working_tree_paths"] = sorted(dirty_paths)
    if run.working_tree_diffs:
        console.print(
            f"[dim]Uncommitted changes: [bold]{len(run.working_tree_diffs)}[/bold] file(s)[/dim]"
        )


def _repair_before_detection(opts: UpdateOptions, run: UpdateRun) -> None:
    """Repairs that must not wait for a new commit; skipped on a dry run."""
    if opts.dry_run:
        return
    _repair_module_attribution(run.repo_path)
    # Opt-in edit capture binds pending agent edits before the git walk.
    with contextlib.suppress(Exception):
        from repowise.core.edit_capture.binder import bind_repo

        bind_repo(Path(run.repo_path))


def _assess_staleness(run: UpdateRun) -> None:
    """Decide whether git, the analyzers or the store leave anything to do."""
    state = run.state
    run.analyzer_changed = health_analyzer_changed(state)
    # Git rows written under older history rules force one full walk.
    run.history_stale = git_history_stale(state)
    run.git_is_current = bool(
        run.head
        and run.head == run.base_ref
        and not run.config.changed
        and not run.renderer_changed
        and not run.working_tree_diffs
        and not run.analyzer_changed
        and not run.history_stale
    )
    # A store read, so only asked when the run would otherwise exit.
    run.extraction_changed = run.git_is_current and parser_changed(run.repo_path)
    if run.extraction_changed:
        run.git_is_current = False
    _announce_rework(run)
    # Not in ``git_is_current``: the rescan parses on its own, and every exit that
    # skips the re-parse runs it instead.
    run.scanner_changed = security_scanner_changed(state)
    if run.scanner_changed:
        console.print(
            "[yellow]Security scanner changed since this index was scanned; "
            "rescanning.[/yellow]"
        )
    # Pages stale for reasons no commit explains; read only when git is current.
    if run.git_is_current:
        from repowise.core.persistence import load_stale_update_targets

        run.stale_db_paths, run.stale_deterministic_ids = load_stale_update_targets(
            run.repo_path
        )


def _announce_rework(run: UpdateRun) -> None:
    """Say why a run with no new commit still re-scores, re-parses or re-renders."""
    if run.analyzer_changed or run.extraction_changed:
        # Falls through to a re-parse that re-scores and writes no pages.
        console.print(
            "[yellow]Health analyzer changed since this index was scored; re-scoring.[/yellow]"
            if run.analyzer_changed
            else "[yellow]Parser changed since this index was built; re-parsing.[/yellow]"
        )
    if run.renderer_changed:
        console.print(
            "[yellow]Wiki renderer changed since these pages were written; "
            "re-rendering structural pages once, no model needed.[/yellow]"
        )
    if run.history_stale:
        console.print(
            "[yellow]Re-reading git history once to label commits from the "
            "attribution backfill.[/yellow]"
            if run.state.get(GIT_HISTORY_VERSION_KEY) == GIT_HISTORY_BACKFILL_REWALK
            else "[yellow]Git history rules changed since this index was built; "
            "re-reading the full history once.[/yellow]"
        )


def _already_up_to_date(opts: UpdateOptions, run: UpdateRun) -> UpdateOutcome:
    """Nothing changed: refresh stamps under the lock, show notices, exit NOOP."""
    console.print("[green]Already up to date.[/green]")
    # A template wiki is current but has no model-written pages yet.
    if resolve_docs_mode(run.state) == "deterministic":
        console.print(
            "[dim]Documentation is built from code. "
            "Run [bold]repowise generate[/bold] to write pages with a model.[/dim]"
        )
    if not opts.dry_run:
        _heal_up_to_date_store(opts, run)
    repo_path = run.repo_path
    # Best-effort: a notice must never turn a clean no-op into an error.
    try:
        from repowise.cli.upgrade import assess_store

        _surface_reindex_recommendation(
            repo_path, assess_store(repo_path), emitter=run.emitter, dry_run=opts.dry_run
        )
    except Exception as exc:
        log.debug("reindex_notice_skipped", error=str(exc))
    # This path skips generation, so it is the only place to name missing slots.
    from .commit_limit_notice import surface_pinned_commit_limit
    from .slot_notice import surface_missing_slots

    surface_missing_slots(repo_path, emitter=run.emitter, dry_run=opts.dry_run)
    surface_pinned_commit_limit(repo_path, emitter=run.emitter, dry_run=opts.dry_run)
    return _emit_done(run, UpdateOutcome.NOOP)


def _heal_up_to_date_store(opts: UpdateOptions, run: UpdateRun) -> None:
    """Backfill fingerprints and re-stamp the store on the "up to date" path."""
    repo_path, state, head = run.repo_path, run.state, run.head
    # Backfill fingerprints an older index never wrote; an unset renderer stamp
    # would hide the next renderer upgrade.
    needs_backfill = (
        run.config.prev_fingerprint is None
        or run.prev_renderer_fp is None
        or not isinstance(state.get("config_dependency_fingerprints"), dict)
    )
    # Writes only under the single-flight lock; if another update holds it, that
    # run heals instead.
    heal_lock = try_acquire_update_lock(repo_path, head)
    if heal_lock is not None:
        log.debug("self_heal_skipped", reason="another update holds the lock")
        return
    try:
        rescanned = run.scanner_changed and rescan_security_if_changed(
            repo_path, state, list(run.cfg.get("exclude_patterns") or [])
        )
        if needs_backfill or rescanned:
            run.timings.stop("run")
            save_state(
                repo_path,
                {
                    **state,
                    "config_fingerprint": run.config.curr_fingerprint,
                    "config_dependency_fingerprints": run.config.curr_dependency_fps,
                    "renderer_fingerprint": run.curr_renderer_fp,
                    "phase_timings": run.timings.totals,
                },
            )
        stamp_head_commit(repo_path, head)
        heal_commit_offsets(repo_path)
        _finish_outcome(repo_path, opts.agents_md)
        # Current index: any pending marker is caught up.
        consume_update_pending(repo_path, head)
    finally:
        release_update_lock(repo_path)


def _detect_changes(run: UpdateRun) -> None:
    """The diffed files since the base, plus stale pages whose files are gone."""
    from repowise.core.ingestion.change_detector import merge_file_diffs

    with timed(run.timings, "detect_changes"):
        run.file_diffs = merge_file_diffs(
            run.detector.get_changed_files(run.base_ref, run.head or "HEAD"),
            run.working_tree_diffs,
        )

    # Loaded already when git is current; otherwise load now so old structural
    # debt is carried forward.
    if not run.git_is_current:
        from repowise.core.persistence import load_stale_update_targets

        run.stale_db_paths, run.stale_deterministic_ids = load_stale_update_targets(
            run.repo_path
        )

    # A stale page whose file is gone goes through the deletion path.
    if run.stale_db_paths:
        _tombstone_missing_stale_pages(run)


def _tombstone_missing_stale_pages(run: UpdateRun) -> None:
    from repowise.core.ingestion.change_detector import FileDiff

    present, absent = [], []
    for path in run.stale_db_paths:
        (present if (run.repo_path / path).exists() else absent).append(path)
    run.stale_db_paths = present
    known = {fd.path for fd in run.file_diffs}
    run.file_diffs = [
        *run.file_diffs,
        *(
            FileDiff(path, "deleted", None, None, None, None)
            for path in absent
            if path not in known
        ),
    ]
    if absent:
        console.print(f"Stale pages for deleted files: [cyan]{len(absent)}[/cyan]")


def _acquire_update_lock(run: UpdateRun) -> UpdateOutcome | None:
    """Take the single-flight lock, or leave HEAD pending for the update holding it."""
    repo_path, head = run.repo_path, run.head
    existing_lock = try_acquire_update_lock(repo_path, head)
    if existing_lock is not None:
        _report_lock_held(run, existing_lock)
        from repowise.cli.platform import telemetry

        # Not a failure: another update holds the lock and this run defers to it.
        telemetry.add_command_outcome(outcome="deferred")
        return _emit_done(run, UpdateOutcome.DEFERRED)

    import atexit

    # The lock now owns the hook's suppression window; drop the queued marker.
    clear_update_queued(repo_path)
    atexit.register(release_update_lock, repo_path)
    atexit.register(clear_update_queued, repo_path)
    return None


def _report_lock_held(run: UpdateRun, existing_lock: dict) -> None:
    from repowise.core.update_lock import (
        UPDATE_LOCK_SUSPECT_AFTER_SECONDS,
        format_lock_age,
        lock_age_seconds,
    )

    head = run.head
    age = lock_age_seconds(existing_lock)
    target_short = (existing_lock.get("target_commit") or "")[:8]
    write_update_pending(run.repo_path, head)
    console.print(
        f"[yellow]Another `repowise update` is already running "
        f"(pid {existing_lock.get('pid')}, target {target_short}, "
        f"holding the lock {format_lock_age(age)}).[/yellow]"
    )
    # A live owner is never evicted on age; say where to look instead.
    if age is not None and age > UPDATE_LOCK_SUSPECT_AFTER_SECONDS:
        console.print(
            "[yellow]That is longer than an update normally takes. If "
            "[bold].repowise/.update.log[/bold] has not been written to in "
            f"that time, pid {existing_lock.get('pid')} is stuck and can be "
            "stopped; this repo will update on the next run.[/yellow]"
        )
    console.print(
        f"[dim]HEAD {head[:8] if head else 'HEAD'} marked as pending; "
        "the running update will roll forward to it. This run regenerated "
        "nothing; watch [bold].repowise/.update.log[/bold] for the one in "
        "flight.[/dim]"
    )


def _prepare_locked_run(opts: UpdateOptions, run: UpdateRun) -> None:
    """Under the lock: backfill the docs mode, upgrade the store, show notices."""
    repo_path, state = run.repo_path, run.state
    # Backfill the docs mode on legacy state, never from a one-off --docs/--no-docs.
    explicit_override = (
        # index_only=True always counts as an override.
        opts.index_only or opts.docs_flag is not None
    )
    if "docs_mode" not in state and not explicit_override:
        state.update(docs_mode_state_fields(resolve_docs_mode(state)))
        save_state(repo_path, state)

    _assess_store_upgrade(opts, run)

    # This run only reaches changed files, so name the command for missing slots.
    from .commit_limit_notice import surface_pinned_commit_limit
    from .slot_notice import surface_missing_slots

    surface_missing_slots(repo_path, emitter=run.emitter, dry_run=opts.dry_run)
    surface_pinned_commit_limit(repo_path, emitter=run.emitter, dry_run=opts.dry_run)

    render_header(repo_path, run.base_ref, run.head)


def _assess_store_upgrade(opts: UpdateOptions, run: UpdateRun) -> None:
    """Run the store upgrade's no-model actions; reindex is only ever recommended."""
    repo_path = run.repo_path
    try:
        from repowise.cli.upgrade import apply_upgrade, assess_store

        verdict = assess_store(repo_path)
        if not verdict.is_noop:
            _surface_reindex_recommendation(
                repo_path, verdict, emitter=run.emitter, dry_run=opts.dry_run
            )
            if verdict.actions and not opts.dry_run:
                for action in verdict.actions:
                    console.print(f"[dim]Upgrade: {action.reason}[/dim]")
                run_async(apply_upgrade(repo_path, verdict))
        # Interactive terminals only, so the post-commit hook never prints it.
        if console.is_terminal and run.emitter is None:
            _surface_release_news(written_by=verdict.written_by)
    except Exception as exc:  # never block a routine update on the upgrade layer
        log.debug("store_upgrade_skipped", error=str(exc))


def _nothing_to_do(run: UpdateRun) -> bool:
    return not (
        run.file_diffs
        or run.config.changed
        or run.renderer_changed
        or run.analyzer_changed
        or run.extraction_changed
        or run.history_stale
        or run.stale_db_paths
        or run.stale_deterministic_ids
    )


def _no_changed_files(opts: UpdateOptions, run: UpdateRun) -> UpdateOutcome:
    """Nothing changed in the range: advance the pointers and exit NOOP."""
    repo_path, state, head = run.repo_path, run.state, run.head
    console.print("[green]No changed files detected.[/green]")
    if run.scanner_changed and not opts.dry_run:
        rescan_security_if_changed(repo_path, state, list(run.cfg.get("exclude_patterns") or []))
    # Advance the sync pointer (and the docs pointer: no docs work is pending).
    persisted = {
        **state,
        "last_sync_commit": head,
        "config_fingerprint": run.config.curr_fingerprint,
        "config_dependency_fingerprints": run.config.curr_dependency_fps,
        "renderer_fingerprint": run.curr_renderer_fp,
    }
    # The widened range held nothing, so the repair is done. Gated as the widening
    # is: another base says nothing about the marker's range.
    if opts.since is None and run.index_only:
        persisted.pop("pending_repair", None)
    if not run.index_only and head:
        persisted["last_docs_commit"] = head
    run.timings.stop("run")
    persisted["phase_timings"] = run.timings.totals
    save_state(repo_path, persisted)
    # The server reads head_commit from the DB row, not state.json.
    stamp_head_commit(repo_path, head)
    # No git phase here, so backfill commit offsets directly.
    if not opts.dry_run:
        heal_commit_offsets(repo_path)
    _finish_outcome(repo_path, opts.agents_md, write=not opts.dry_run)
    consume_update_pending(repo_path, head)
    return _emit_done(run, UpdateOutcome.NOOP)


def _config_change_only(run: UpdateRun) -> bool:
    """A config edit with no commits to index; those fall through to the full path."""
    config = run.config
    return config.changed and not (
        run.file_diffs or config.git_changed or config.generation_changed or run.history_stale
    )


def _config_only_update(opts: UpdateOptions, run: UpdateRun) -> UpdateOutcome:
    """Re-score health (or just record the new config) without re-indexing."""
    repo_path, head, config = run.repo_path, run.head, run.config
    if opts.dry_run:
        action = "health would be re-scored" if config.health_changed else "state would be updated"
        console.print(f"[yellow]Dry run: {action}. No changes made.[/yellow]")
        return _emit_done(run, UpdateOutcome.DRY_RUN)
    if config.health_changed:
        cfg = load_config(repo_path)
        exclude_patterns = list(cfg.get("exclude_patterns") or [])
        _stage(run, "rescore_health")
        with _errors_reported(run):
            _run_full_health_rescore(
                repo_path,
                exclude_patterns,
                run.state,
                head,
                config.curr_fingerprint,
                dependency_fingerprints=config.curr_dependency_fps,
                timings=run.timings,
            )
    else:
        updated_state = {
            **run.state,
            "last_sync_commit": head,
            "config_fingerprint": config.curr_fingerprint,
            "config_dependency_fingerprints": config.curr_dependency_fps,
        }
        run.timings.stop("run")
        updated_state["phase_timings"] = run.timings.totals
        save_state(repo_path, updated_state)
    stamp_head_commit(repo_path, head)
    _finish_outcome(repo_path, opts.agents_md)
    consume_update_pending(repo_path, head)
    return _emit_done(run, UpdateOutcome.REGENERATED)


def _rebuild_index(run: UpdateRun) -> None:
    """Re-parse the changed files and rebuild the graph and git metadata."""
    run.exclude_patterns = list(run.cfg.get("exclude_patterns") or [])

    _stage(run, "rebuild_graph")

    # Idle files' decay-only rows go to git_decay_map, outside git_meta_map, so
    # partial health's repo-wide aggregates are unaffected.
    run.working_tree_only = _working_tree_only(run)
    run.head_ts = None if run.working_tree_only else _head_commit_ts(run.repo_path)
    (
        run.parsed_files,
        run.source_map,
        run.graph_builder,
        run.repo_structure,
        run.file_count,
        run.git_meta_map,
    ) = _rebuild_graph_and_git(
        run.repo_path,
        run.file_diffs,
        run.cfg,
        run.exclude_patterns,
        git_tier=run.state.get("git_tier"),
        include_submodules=bool(run.state.get("include_submodules", False)),
        include_nested_repos=bool(run.state.get("include_nested_repos", False)),
        idle_decay_sink=None if run.working_tree_only else run.git_decay_map,
        # A full re-render quotes every file's history, as init does.
        force_full_git=run.config.git_changed or run.history_stale or run.renderer_changed,
        git_summary_sink=run.full_git_summaries,
        timings=run.timings,
    )


def _working_tree_only(run: UpdateRun) -> bool:
    """Only uncommitted files changed, so the history-driven phases have nothing new."""
    return bool(
        run.working_tree_diffs
        and run.head
        and run.head == run.base_ref
        and not (
            run.config.health_changed
            or run.config.generation_changed
            or run.renderer_changed
            or run.analyzer_changed
            or run.extraction_changed
            or run.history_stale
        )
    )


def _plan_pages(opts: UpdateOptions, run: UpdateRun) -> None:
    """Pick the pages to regenerate and the pages to only mark stale."""
    _stage(run, "plan_pages")

    run.timings.start("plan_pages")
    run.cascade_budget = opts.cascade_budget
    if run.cascade_budget is None:
        from repowise.core.ingestion.change_detector import compute_adaptive_budget

        run.cascade_budget = compute_adaptive_budget(run.file_diffs, run.file_count)
        if opts.verbose:
            console.print(f"Adaptive cascade budget: [cyan]{run.cascade_budget}[/cyan]")
    # The builder's cached pagerank spares a full-graph pass.
    from .deterministic import load_stale_file_page_ages

    run.affected = run.detector.get_affected_pages(
        run.file_diffs,
        run.graph_builder.graph(),
        run.cascade_budget,
        pagerank=run.graph_builder.pagerank(),
        stale_pages=load_stale_file_page_ages(run.repo_path),
    )

    # Pages an older renderer wrote: the cascade only reaches changed files.
    stale_renderer_paths = _stale_renderer_paths(run.repo_path, run.parsed_files)
    run.timings.stop("plan_pages")
    _add_stale_pages(run, stale_renderer_paths)
    _widen_for_full_render(run)
    _report_page_plan(run)


def _add_stale_pages(run: UpdateRun, stale_renderer_paths: list[str]) -> None:
    if stale_renderer_paths:
        console.print(f"Pages from an older renderer: [cyan]{len(stale_renderer_paths)}[/cyan]")
    # Ceiling: a stale page whose file is no longer indexed is never re-rendered
    # and costs a full reparse on each idle run until it is retired.
    if run.stale_db_paths:
        console.print(f"Reconciling stale structural pages: [cyan]{len(run.stale_db_paths)}[/cyan]")
    if run.stale_deterministic_ids:
        console.print(
            "Reconciling stale repository structure: "
            f"[cyan]{len(run.stale_deterministic_ids)}[/cyan]"
        )
    stale_extra = list(dict.fromkeys([*stale_renderer_paths, *run.stale_db_paths]))
    if stale_extra:
        run.affected.regenerate = list(dict.fromkeys([*run.affected.regenerate, *stale_extra]))


def _widen_for_full_render(run: UpdateRun) -> None:
    affected = run.affected
    if run.config.generation_changed or run.renderer_changed:
        # A generation-config or renderer change re-renders the full page set.
        affected.regenerate = [pf.file_info.path for pf in run.parsed_files]
    if run.renderer_changed and not run.config.generation_changed and not run.index_only:
        # A model wiki: model-written levels are only marked stale, for an opt-in
        # `generate --stale`.
        from .deterministic import load_cascade_overflow_split

        affected.decay_only = list(dict.fromkeys([*affected.decay_only, *affected.regenerate]))
        split = load_cascade_overflow_split(run.repo_path, affected.regenerate)
        if split and split[0]:
            console.print(
                f"  {split[0]} pages written by a model are marked stale: run "
                "[cyan]repowise generate --stale[/cyan] to rewrite them "
                "(it estimates the cost first)."
            )


def _report_page_plan(run: UpdateRun) -> None:
    affected = run.affected
    if affected.stale_due_to_budget > 0:
        from .deterministic import load_cascade_overflow_split
        from .reporting import render_cascade_budget_warning

        # The detector puts the budget overflow first in decay_only.
        skipped = affected.decay_only[: affected.stale_due_to_budget]
        render_cascade_budget_warning(
            run.cascade_budget,
            affected.stale_due_to_budget,
            load_cascade_overflow_split(run.repo_path, skipped),
        )

    console.print(f"Pages to regenerate: [cyan]{len(affected.regenerate)}[/cyan]")
    if affected.decay_only:
        console.print(f"Pages to decay: [yellow]{len(affected.decay_only)}[/yellow]")


def _analyze_changes(run: UpdateRun) -> None:
    """Partial health, dead code and doc drift over the changed files."""
    # Unchanged files' pages need the complexity only the store still holds.
    from .deterministic import hydrate_symbol_complexity

    hydrate_symbol_complexity(
        run.repo_path,
        run.parsed_files,
        set(run.affected.regenerate) - {fd.path for fd in run.file_diffs},
    )

    # Stored git rows and p80 keep the dead-code and hotspot gates repo-wide.
    if not run.working_tree_only:
        _run_partial_analyses(run)

    # The transient BlameIndex must not reach persistence or generation.
    from repowise.core.pipeline.phases.git import drop_transient_git_signals

    drop_transient_git_signals(list(run.git_meta_map.values()))
    # No new commit: an empty map keeps the persist from walking for commits.
    run.persisted_git_meta = {} if run.working_tree_only else run.git_meta_map


def _run_partial_analyses(run: UpdateRun) -> None:
    repo_path = run.repo_path
    with timed(run.timings, "analysis.stored_reads"):
        stored_git_meta = _load_stored_git_meta(repo_path)
        stored_performance_callers = _load_stored_performance_callers(repo_path, run.file_diffs)
        repo_function_mod_p80 = _load_stored_function_mod_p80(repo_path)
    run.partial_health_report, run.dead_code_report = _run_partial_analysis(
        repo_path,
        run.graph_builder,
        run.git_meta_map,
        run.parsed_files,
        run.file_diffs,
        run.source_map,
        stored_git_meta=stored_git_meta,
        stored_performance_callers=stored_performance_callers,
        repo_function_mod_p80=repo_function_mod_p80,
        timings=run.timings,
        dead_code=not _dead_code_unchanged(run),
    )
    run.doc_drift_report = _run_doc_drift_partial(
        run.graph_builder,
        run.source_map,
        repo_path=repo_path,
        timings=run.timings,
        base_ref=run.base_ref,
        file_diffs=run.file_diffs,
    )


def _is_prose(path: str) -> bool:
    from repowise.core.ingestion.languages.registry import REGISTRY

    suffix = Path(path).suffix.lower()
    # .mdx imports mark components reachable; .rst is read for symbol names.
    return suffix != ".mdx" and REGISTRY.from_extension(suffix) in ("markdown", "asciidoc")


def _dead_code_unchanged(run: UpdateRun) -> bool:
    """Whether the stored dead-code findings still stand and nothing this run reads new ones.

    True only when every changed file is prose, no analyzer, parser, renderer,
    history or config change forces a re-score, and no page render in this run
    consumes the report. (The knowledge graph takes the report but does not read it.)
    """
    config = run.config
    if (
        run.analyzer_changed
        or run.extraction_changed
        or run.renderer_changed
        or run.history_stale
        or config.changed
    ):
        return False
    changed = [path for fd in run.file_diffs for path in (fd.path, fd.old_path) if path]
    if not all(map(_is_prose, changed)):
        return False
    # Index-only renders template wikis only.
    if run.index_only and resolve_docs_mode(run.state) != "deterministic":
        return True
    rendered = run.affected.regenerate if run.affected is not None else []
    return all(map(_is_prose, rendered)) and not run.stale_deterministic_ids


def _refresh_knowledge_graph_stage(run: UpdateRun) -> None:
    """Rebuild the knowledge graph when its fingerprint moved (not before a commit)."""
    if not run.working_tree_only:
        with timed(run.timings, "knowledge_graph"):
            run.knowledge_graph_result = _refresh_knowledge_graph(
                run.repo_path,
                run.parsed_files,
                run.graph_builder,
                run.repo_structure,
                run.git_meta_map,
                run.dead_code_report,
                (run.state.get("knowledge_graph") or {}).get("fingerprint"),
            )
    if run.knowledge_graph_result is None:
        return
    if run.config.generation_changed:
        # Generation reads the persisted KG, so publish it before rendering.
        from repowise.cli.state_persistence import save_knowledge_graph_json

        try:
            save_knowledge_graph_json(run.repo_path, run.knowledge_graph_result)
        except Exception as exc:
            raise RuntimeError(
                "Configuration-driven knowledge-graph refresh failed; the previous "
                "fingerprint was retained so the next update retries."
            ) from exc
    elif run.renderer_changed:
        # A file's layer comes from the KG.
        from repowise.cli.state_persistence import save_knowledge_graph_json

        try:
            save_knowledge_graph_json(run.repo_path, run.knowledge_graph_result)
        except Exception as exc:
            run.degraded.append(f"Knowledge-graph publish before render: {exc}")


@dataclass
class _TemplatePages:
    """What the index-only template render produced."""

    docs_mode: str
    pages: list = field(default_factory=list)
    render_stats: dict[str, int] = field(default_factory=dict)
    # False after a failed renderer-driven render: keep the old stamp and pages.
    renderer_render_ok: bool = True


def _index_only_update(opts: UpdateOptions, run: UpdateRun) -> UpdateOutcome:
    """Index-only: re-render a template wiki (no model), persist, and finish."""
    docs_mode = resolve_docs_mode(run.state)
    pages = _TemplatePages(docs_mode=docs_mode, renderer_render_ok=docs_mode != "llm")
    if pages.docs_mode == "deterministic":
        _render_template_pages(opts, run, pages)
    _stage(run, "persist")
    with _errors_reported(run):
        _persist_index_only(run, pages)
    repo_path = run.repo_path
    _finish_outcome(repo_path, opts.agents_md, run.degraded)
    consume_update_pending(repo_path, run.head)
    _record_update_outcome(
        index_only=True,
        changed_count=len(run.file_diffs),
        generated_pages=pages.pages,
        docs_mode=pages.docs_mode,
    )
    if pages.render_stats.get("embed_failed_pages"):
        from .deterministic import deterministic_embedder_name

        _fail_on_embed_failure(
            repo_path,
            deterministic_embedder_name(run.cfg),
            pages.render_stats["embed_failed_pages"],
            run.emitter,
        )
    return _emit_done(
        run,
        UpdateOutcome.REGENERATED,
        pages_generated=len(pages.pages),
        degraded=run.degraded,
    )


def _render_template_pages(opts: UpdateOptions, run: UpdateRun, out: _TemplatePages) -> None:
    from .deterministic import (
        load_prior_page_ids,
        persist_deterministic_pages,
    )

    _stage(run, "generate")
    prior_ids = load_prior_page_ids(run.repo_path)
    full_scope = run.config.generation_changed or run.renderer_changed

    degraded = run.degraded
    degraded_before_render = len(degraded)
    with timed(run.timings, "render"):
        out.pages = _render_template_page_set(opts, run, out, prior_ids, full_scope)
    out.renderer_render_ok = not run.renderer_changed or (
        bool(out.pages) and len(degraded) == degraded_before_render
    )
    if run.config.generation_changed and len(degraded) > degraded_before_render:
        raise RuntimeError(
            "Configuration-driven page generation failed; the previous "
            "fingerprint was retained so the next update retries."
        )

    if out.pages:
        # Its own session, so its own timing row.
        with timed(run.timings, "render.persist"):
            run.state["total_pages"] = persist_deterministic_pages(
                repo_path=run.repo_path,
                generated_pages=out.pages,
                # Cascade-reached pages the render did not touch.
                decay_paths=run.affected.decay_only,
                degraded=degraded,
            )
        if run.head:
            run.state["last_docs_commit"] = run.head
        console.print(
            f"  [green]✓[/green] Rebuilt [bold]{len(out.pages):,}[/bold] "
            "documentation pages from code"
        )


def _render_template_page_set(
    opts: UpdateOptions, run: UpdateRun, out: _TemplatePages, prior_ids, full_scope: bool
) -> list:
    from .deterministic import (
        regenerate_deterministic_page_ids,
        regenerate_deterministic_pages,
    )

    pages = regenerate_deterministic_pages(
        repo_path=run.repo_path,
        parsed_files=run.parsed_files,
        source_map=run.source_map,
        graph_builder=run.graph_builder,
        repo_structure=run.repo_structure,
        git_meta_map=run.git_meta_map,
        regenerate_paths=run.affected.regenerate,
        cfg=run.cfg,
        concurrency=opts.concurrency,
        degraded=run.degraded,
        dead_code_report=run.dead_code_report,
        prior_page_ids=prior_ids,
        full_scope=full_scope,
        stats_out=out.render_stats,
    )
    if run.stale_deterministic_ids and not full_scope:
        pages.extend(
            regenerate_deterministic_page_ids(
                repo_path=run.repo_path,
                parsed_files=run.parsed_files,
                source_map=run.source_map,
                graph_builder=run.graph_builder,
                repo_structure=run.repo_structure,
                git_meta_map=run.git_meta_map,
                page_ids=run.stale_deterministic_ids,
                cfg=run.cfg,
                concurrency=opts.concurrency,
                degraded=run.degraded,
                dead_code_report=run.dead_code_report,
                prior_page_ids=prior_ids,
                stats_out=out.render_stats,
            )
        )
    return pages


def _persist_index_only(run: UpdateRun, pages: _TemplatePages) -> None:
    config = run.config
    template_wiki = pages.docs_mode == "deterministic"
    persisted_changed_paths = (
        [pf.file_info.path for pf in run.parsed_files]
        if config.traversal_changed
        else [fd.path for fd in run.file_diffs]
    )
    config_vector_store = _config_vector_store(run, pages, template_wiki)
    _persist_index_only_update(
        run.repo_path,
        run.graph_builder,
        run.persisted_git_meta,
        run.dead_code_report,
        run.partial_health_report,
        run.state,
        run.head,
        run.start,
        persisted_changed_paths,
        doc_drift_report=run.doc_drift_report,
        file_diffs=run.file_diffs,
        knowledge_graph_result=run.knowledge_graph_result,
        parsed_files=run.parsed_files,
        source_map=run.source_map,
        degraded=run.degraded,
        # The repo's docs mode, not this run's page count.
        template_wiki=template_wiki,
        pages_rendered=len(pages.pages),
        git_decay_map=run.git_decay_map,
        exclude_patterns=run.exclude_patterns,
        head_ts=run.head_ts,
        force_full_rescore=config.health_changed,
        full_git_summary=(run.full_git_summaries[0] if run.full_git_summaries else None),
        # Only a history-window change invalidates mined decisions.
        history_window_changed=config.git_changed,
        reconcile_full_scope=config.traversal_changed,
        full_generation_page_ids=_full_generation_page_ids(run, pages, template_wiki),
        stamp_renderer=pages.renderer_render_ok,
        dependency_fingerprints=config.curr_dependency_fps,
        vector_store=config_vector_store,
        require_config_rebuild_success=config.rebuild_required,
        timings=run.timings,
    )


def _config_vector_store(run: UpdateRun, pages: _TemplatePages, template_wiki: bool):
    """The vector store a config-driven rebuild writes to, or None without one."""
    config = run.config
    if not (config.traversal_changed or config.generation_changed):
        return None
    vector_store = _build_update_vector_store(
        run.repo_path,
        run.cfg,
        run.degraded,
        required=(config.rebuild_required and template_wiki),
    )
    if config.generation_changed and pages.pages and vector_store is not None:
        _embed_config_pages(vector_store, pages.pages)
    return vector_store


def _full_generation_page_ids(
    run: UpdateRun, pages: _TemplatePages, template_wiki: bool
) -> set[str] | None:
    """Page ids of a complete template render, which retires every page outside it."""
    full_render = run.config.generation_changed or (
        run.renderer_changed and pages.renderer_render_ok
    )
    if template_wiki and full_render:
        return {page.page_id for page in pages.pages}
    return None


def _embed_config_pages(vector_store, pages: list) -> None:
    from repowise.core.persistence.vector_store import embed_item

    embed_items = [
        item
        for page in pages
        if (
            item := embed_item(
                page.page_id,
                title=page.title,
                page_type=page.page_type,
                target_path=page.target_path,
                summary=page.summary,
                content=page.content,
                page_metadata=page.metadata,
                digest=page.digest,
            )
        )
        is not None
    ]
    try:
        run_async(vector_store.embed_batch(embed_items))
    except Exception as exc:
        raise RuntimeError(
            "Configuration-driven vector refresh failed; the previous "
            "fingerprint was retained so the next update retries."
        ) from exc


def _docs_update(opts: UpdateOptions, run: UpdateRun) -> UpdateOutcome:
    """Model-backed update: decisions, page generation, persist, then state."""
    repo_path = run.repo_path
    docs = _start_docs_pass(opts, run)
    run_decision_passes(opts, run, docs)
    generate_pages(opts, run, docs)
    render_stale_structure(opts, run, docs)

    # One transaction, outside the contended generation window.
    from repowise.cli.providers import flush_cost_tracker

    flush_cost_tracker(docs.cost_tracker)
    _enrich_knowledge_graph(run, docs)
    _persist_docs(run, docs)
    _rescore_after_persist(run)

    # The persist above only rescanned the changed files.
    if run.scanner_changed:
        with timed(run.timings, "security_rescan"):
            rescan_security_if_changed(repo_path, run.state, run.exclude_patterns)

    # Saved before the editor files quote it.
    stamp_file_page_scope(
        repo_path, run.state, run.parsed_files, run.graph_builder, run.persisted_git_meta
    )
    save_state(repo_path, run.state)
    _finish_outcome(repo_path, opts.agents_md, run.degraded, timings=run.timings)
    _save_docs_state(run, docs)

    # A pending marker is kept only when it names a newer commit.
    consume_update_pending(repo_path, run.head)
    if not opts.skip_cross_repo_hooks:
        _run_cross_repo_hooks(run)
    return _report_docs_outcome(opts, run, docs)


def _start_docs_pass(opts: UpdateOptions, run: UpdateRun) -> DocsPass:
    """Generation config, the provider (prompting when allowed) and its cost tracker."""
    # Imported here so index-only updates never pay for the generation layer.
    from repowise.core.generation import GenerationConfig

    cfg = run.cfg
    generation_changed = run.config.generation_changed
    language = cfg.get("language", "en")
    enable_onboarding_cfg = bool(cfg.get("enable_onboarding", True))
    config = GenerationConfig.from_repo_config(
        cfg,
        max_concurrency=opts.concurrency,
        language=language,
        reasoning=resolve_reasoning(opts.reasoning, cfg),
        enable_onboarding=enable_onboarding_cfg,
        wiki_style=cfg.get("wiki_style", "comprehensive"),
        cache_enabled=not generation_changed,
        # Levels 3+ describe the whole repo; a changed-file slice would truncate them.
        file_pages_only=not generation_changed,
    )

    # Prompts for a provider only in an interactive terminal.
    provider = resolve_provider_or_prompt(
        opts.provider_name,
        opts.model,
        run.repo_path,
        reasoning=opts.reasoning,
        interactive=_docs_provider_prompt_allowed(run.emitter),
    )

    # Persist this update's model spend to llm_costs, as init does.
    from repowise.cli.providers import build_cost_tracker

    cost_tracker = build_cost_tracker(
        run.repo_path, run.repo_path.name, no_cost_tracking=opts.no_cost_tracking
    )
    provider._cost_tracker = cost_tracker
    return DocsPass(config=config, provider=provider, cost_tracker=cost_tracker)


def _enrich_knowledge_graph(run: UpdateRun, docs: DocsPass) -> None:
    """Model enrichment of a refreshed KG (layer names, summaries), as init does."""
    # Also runs on a builder-version bump with an unchanged graph: restored layer
    # names were written against the old layers.
    kg = run.knowledge_graph_result
    if kg is None:
        return
    try:
        from repowise.core.generation.knowledge_graph import enrich_knowledge_graph

        with timed(run.timings, "knowledge_graph.enrich"):
            run.knowledge_graph_result = run_async(
                enrich_knowledge_graph(
                    kg_skeleton=kg,
                    llm_client=docs.provider,
                    graph_builder=run.graph_builder,
                    repo_structure=run.repo_structure,
                    tech_stack=kg.project.get("tech_stack", []),
                    generated_pages=docs.generated_pages,
                    reasoning=docs.config.reasoning,
                )
            )
    except Exception as exc:
        console.print(f"[yellow]Knowledge-graph enrichment skipped: {exc}[/yellow]")
        run.degraded.append(f"Knowledge-graph enrichment: {exc}")


def _persist_docs(run: UpdateRun, docs: DocsPass) -> None:
    """Persist in one transaction: pages fail loudly, derived layers degrade."""
    from .persistence import _persist_full_update

    config = run.config
    _stage(run, "persist")
    with _errors_reported(run), timed(run.timings, "persist"):
        docs.db_total_pages = _persist_full_update(
            repo_path=run.repo_path,
            repo_name=run.repo_path.name,
            generated_pages=docs.generated_pages,
            file_diffs=run.file_diffs,
            git_meta_map=run.persisted_git_meta,
            new_decision_markers=[*docs.new_decision_markers, *docs.session_decisions],
            decision_vector_store=docs.decision_vector_store,
            provider=docs.provider,
            partial_health_report=run.partial_health_report,
            dead_code_report=run.dead_code_report,
            graph_builder=run.graph_builder,
            knowledge_graph_result=run.knowledge_graph_result,
            degraded=run.degraded,
            doc_drift_report=run.doc_drift_report,
            decay_paths=run.affected.decay_only,
            parsed_files=run.parsed_files,
            source_map=run.source_map,
            git_decay_map=run.git_decay_map,
            full_git_summary=(run.full_git_summaries[0] if run.full_git_summaries else None),
            reconcile_full_scope=config.traversal_changed,
            reconcile_full_generation=config.generation_changed,
            require_config_rebuild_success=config.rebuild_required,
            require_decision_persist_success=config.git_changed,
            history_window_changed=config.git_changed,
            failed_steps=docs.failed_steps,
            timings=run.timings,
        )


def _rescore_after_persist(run: UpdateRun) -> None:
    """Re-score every file off this run's graph: weekly, or forced by a config or parser change."""
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

    from .persistence import full_rescore_due, run_decay_health_rescore

    health_changed = run.config.health_changed
    rescored = False
    if health_changed or run.extraction_changed or full_rescore_due(run.state, run.head_ts):
        with timed(run.timings, "rescore"):
            rescored = run_decay_health_rescore(
                run.repo_path, run.graph_builder, run.parsed_files, run.exclude_patterns
            )
    if rescored:
        # No timestamp (git unavailable): keep the existing stamp.
        if run.head_ts is not None:
            run.state["last_full_rescore_at"] = run.head_ts
        run.state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION
    if health_changed and not rescored:
        raise RuntimeError(
            "Configuration-triggered health re-score failed; the previous "
            "fingerprint was retained so the next update retries."
        )


def _save_docs_state(run: UpdateRun, docs: DocsPass) -> None:
    """Advance both sync pointers and write the run's state."""
    from repowise.cli.helpers import config_fingerprint

    repo_path, state, head = run.repo_path, run.state, run.head
    if run.knowledge_graph_result is not None:
        _export_knowledge_graph(run)

    # Before the pointer moves: the marker keeps the old one. A clean run leaves
    # an earlier marker for the index-only update that widens to it.
    if docs.failed_steps:
        from .persistence import record_repair_marker

        record_repair_marker(state, dict(state), docs.failed_steps)
        console.print(
            "[yellow]Some data for this commit range was not persisted; "
            "the next update will re-cover it.[/yellow]"
        )

    # Never write a null pointer: the next update would refuse to run.
    if head:
        state["last_sync_commit"] = head
        state["last_docs_commit"] = head
    # The DB total; regeneration upserts existing pages.
    state["total_pages"] = docs.db_total_pages
    state["config_fingerprint"] = config_fingerprint(repo_path)
    state["config_dependency_fingerprints"] = run.config.curr_dependency_fps
    state["renderer_fingerprint"] = _current_renderer_fingerprint(repo_path)
    # Closed at the state write, as on the index-only path.
    run.timings.stop("run")
    state["phase_timings"] = run.timings.totals
    if run.full_git_summaries:
        coverage = getattr(run.full_git_summaries[0], "history_coverage", None)
        if coverage is not None:
            state["git_history_coverage"] = coverage.to_dict()
        else:
            state.pop("git_history_coverage", None)
        # Every row was rewritten under the current rules, unless they never
        # reached the store; then the next update walks again.
        if "Git persist" not in docs.failed_steps:
            state[GIT_HISTORY_VERSION_KEY] = GIT_HISTORY_VERSION
    save_state(repo_path, state)


def _export_knowledge_graph(run: UpdateRun) -> None:
    try:
        from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json

        save_knowledge_graph_json(run.repo_path, run.knowledge_graph_result)
        run.state["knowledge_graph"] = build_kg_state(run.knowledge_graph_result)
    except Exception as exc:
        console.print(f"[yellow]Knowledge-graph export skipped: {exc}[/yellow]")
        run.degraded.append(f"Knowledge-graph export: {exc}")


def _run_cross_repo_hooks(run: UpdateRun) -> None:
    """Rebuild the cross-repo layer when this repo is a workspace member."""
    try:
        ws_root = find_workspace_root(run.repo_path)
        if ws_root is not None:
            from repowise.core.workspace import WorkspaceConfig
            from repowise.core.workspace.update import run_cross_repo_hooks

            ws_config = WorkspaceConfig.load(ws_root)
            alias = _workspace_alias(ws_root, ws_config, run.repo_path)
            if alias and len(ws_config.repos) >= 2:
                console.print("Running cross-repo analysis...")
                run_async(run_cross_repo_hooks(ws_config, ws_root, [alias]))
                console.print("[green]Cross-repo analysis updated.[/green]")
    except Exception as exc:
        # Never fails the update, but is not reported as clean either.
        run.degraded.append(f"Cross-repo analysis: {exc}")


def _workspace_alias(ws_root: Path, ws_config: Any, repo_path: Path) -> str | None:
    """This repo's alias in the workspace config, or None when it is not listed."""
    repo_abs = repo_path.resolve()
    for entry in ws_config.repos:
        if (ws_root / entry.path).resolve() == repo_abs:
            return entry.alias
    return None


def _report_docs_outcome(opts: UpdateOptions, run: UpdateRun, docs: DocsPass) -> UpdateOutcome:
    """Record the outcome, then the JSON ``done`` event or the completion panel."""
    elapsed = time.monotonic() - run.start
    _record_update_outcome(
        index_only=False,
        changed_count=len(run.file_diffs),
        provider=docs.provider,
        generated_pages=docs.generated_pages,
    )

    if run.emitter is not None:
        _fail_if_embed_failed(run, docs)
        return _emit_done(
            run,
            UpdateOutcome.REGENERATED,
            pages_generated=len(docs.generated_pages),
            cost_usd=docs.cost_tracker.session_cost,
            duration_s=elapsed,
            degraded=run.degraded,
        )
    show_full_completion(
        generated_pages=docs.generated_pages,
        decay_count=len(run.affected.decay_only),
        decisions_changed=(
            len(docs.new_decision_markers) + len(docs.session_decisions) + docs.decisions_evolved
        ),
        provider=docs.provider,
        cost=docs.cost_tracker.session_cost,
        tokens=docs.cost_tracker.session_tokens,
        elapsed=elapsed,
        degraded=run.degraded,
    )
    _render_update_report(
        docs.generated_pages, run.affected, docs.new_decision_markers, elapsed, detail=opts.verbose
    )
    _fail_if_embed_failed(run, docs)
    return UpdateOutcome.REGENERATED


def _fail_if_embed_failed(run: UpdateRun, docs: DocsPass) -> None:
    if docs.full_stats["embed_failed_pages"]:
        from repowise.cli.providers import resolve_embedder

        _fail_on_embed_failure(
            run.repo_path,
            resolve_embedder(run.cfg.get("embedder")),
            docs.full_stats["embed_failed_pages"],
            run.emitter,
        )


def run_update(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    since: str | None,
    reasoning: str | None,
    cascade_budget: int | None,
    dry_run: bool,
    workspace: bool,
    no_workspace: bool,
    repo_alias: str | None,
    index_only: bool = False,
    docs_flag: bool | None = None,
    full: bool = False,
    yes: bool = False,
    agents_md: bool | None = None,
    concurrency: int = 10,
    no_cost_tracking: bool = False,
    verbose: bool = False,
    progress: str = "rich",
    skip_cross_repo_hooks: bool = False,
    include_working_tree: bool = False,
) -> UpdateOutcome:
    """Incrementally update wiki pages for files changed since last sync.

    If `since` is None, the base commit is read from state.json's last_sync_commit.

    ``skip_cross_repo_hooks`` is set only by the workspace docs flow, which
    calls this per repo and then runs the cross-repo hooks once over every
    updated repo instead of once per repo.

    ``include_working_tree`` folds uncommitted work (staged, unstaged and
    untracked) into the change set on top of the commit range. Off by default,
    because every other caller is anchored to a commit — the post-commit hook,
    a webhook, a manual sync — and indexing a half-finished edit under those is
    not what the user asked for. ``repowise watch`` sets it: watching for file
    saves and then only ever diffing commit-to-commit is what made the watcher
    a no-op until you committed. ``repowise update --working-tree`` sets it on
    request.
    """
    opts = UpdateOptions(
        path=path,
        provider_name=provider_name,
        model=model,
        since=since,
        reasoning=reasoning,
        cascade_budget=cascade_budget,
        dry_run=dry_run,
        workspace=workspace,
        no_workspace=no_workspace,
        repo_alias=repo_alias,
        index_only=index_only,
        docs_flag=docs_flag,
        full=full,
        yes=yes,
        agents_md=agents_md,
        concurrency=concurrency,
        no_cost_tracking=no_cost_tracking,
        verbose=verbose,
        progress=progress,
        skip_cross_repo_hooks=skip_cross_repo_hooks,
        include_working_tree=include_working_tree,
    )
    run = _start_run(opts)
    target = _resolve_target(opts)
    if target.is_workspace:
        return _update_workspace_target(opts, run, target)

    # --- Single-repo path from here on. ---
    run.repo_path = target.repo_path
    assert run.repo_path is not None  # single mode always sets repo_path
    _prepare_repo(run.repo_path)

    # --full reuses the persisted graph instead of diffing changed files.
    if opts.full:
        return _full_upgrade(opts, run)
    _load_sync_base(opts, run)
    _check_config_and_renderer(run)
    _collect_working_tree(opts, run)
    _repair_before_detection(opts, run)
    _assess_staleness(run)
    if run.git_is_current and not run.stale_db_paths and not run.stale_deterministic_ids:
        return _already_up_to_date(opts, run)

    deferred = _acquire_update_lock(run)
    if deferred is not None:
        return deferred
    _prepare_locked_run(opts, run)

    _stage(run, "detect_changes")
    _detect_changes(run)
    if _nothing_to_do(run):
        return _no_changed_files(opts, run)

    if run.config.health_changed:
        console.print("[yellow]Config files changed. Re-running health analysis.[/yellow]")
    if _config_change_only(run):
        return _config_only_update(opts, run)

    render_changed_files(run.file_diffs, verbose=opts.verbose)
    _rebuild_index(run)
    _plan_pages(opts, run)
    if opts.dry_run:
        console.print("[yellow]Dry run: no pages regenerated.[/yellow]")
        return _emit_done(run, UpdateOutcome.DRY_RUN)
    _analyze_changes(run)
    _refresh_knowledge_graph_stage(run)
    if run.index_only:
        return _index_only_update(opts, run)

    return _docs_update(opts, run)

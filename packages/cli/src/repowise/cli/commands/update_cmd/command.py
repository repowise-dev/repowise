"""``repowise update`` — incremental wiki regeneration for changed files.

This module holds the Click command + single-repo orchestration. The standalone
helpers it relies on live in sibling modules: mode resolution (:mod:`.mode`),
core delegators (:mod:`.incremental`), persistence + re-score
(:mod:`.persistence`), the workspace flow (:mod:`.workspace`), and report
rendering (:mod:`.reporting`).
"""

from __future__ import annotations

import sys
import time
from enum import StrEnum
from typing import Any

import click
import structlog

from repowise.cli._setup import configure_cli_logging
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
    silence_logs_for_machine_output,
    try_acquire_update_lock,
    write_update_pending,
)
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode
from repowise.core.reasoning import REASONING_MODES

from .incremental import (
    _build_update_vector_store,
    _rebuild_graph_and_git,
    _refresh_knowledge_graph,
    _run_partial_analysis,
)
from .mode import _resolve_index_only_mode
from .persistence import (
    _persist_index_only_update,
    _run_full_health_rescore,
    stamp_head_commit,
)
from .reporting import (
    JsonProgressEmitter,
    _render_update_report,
    make_generation_progress,
    render_changed_files,
    render_header,
    show_full_completion,
)
from .workspace import _workspace_update

log = structlog.get_logger(__name__)


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

    Coarse buckets + enums only (changed-files bucket, docs mode, provider,
    pages bucket). Best-effort; never breaks the command.
    """
    try:
        from repowise.cli.platform import telemetry

        # docs_mode carries the same three values as the state field of that
        # name; it was a bool until an index-only update started re-rendering
        # template pages, which that shape could not express. It comes from the
        # repo's persisted mode, not from this run's page count: a commit that
        # touched no source file re-renders nothing, and that is not the same
        # as the repo having no wiki.
        pages = generated_pages or []
        if docs_mode is None:
            docs_mode = "llm" if (not index_only and provider is not None) else "none"
        outcome: dict[str, Any] = {
            "outcome": "success",
            "index_only": bool(index_only),
            "docs_mode": docs_mode,
            "changed_files_bucket": telemetry.bucket_count(changed_count),
        }
        if not index_only and provider is not None:
            outcome["provider"] = getattr(provider, "provider_name", None)
            outcome["model"] = getattr(provider, "model_name", None)
        if pages:
            outcome["pages_bucket"] = telemetry.bucket_count(len(pages))
        telemetry.add_command_outcome(**{k: v for k, v in outcome.items() if v is not None})
    except Exception:
        return


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
        "Upgrade an index with a model: backfill the git tier ESSENTIAL -> "
        "FULL if it was built with `--mode fast`, and write the pages with a "
        "provider, replacing a template wiki from `--index-only`. Incremental: "
        "reuses the persisted graph instead of re-parsing and re-resolving it. "
        "Single-repo only."
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
) -> None:
    """Incrementally update wiki pages for files changed since last sync.

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
) -> UpdateOutcome:
    """Incrementally update wiki pages for files changed since last sync.

    If `since` is None, the base commit is read from state.json's last_sync_commit.

    ``skip_cross_repo_hooks`` is set only by the workspace docs flow, which
    calls this per repo and then runs the cross-repo hooks once over every
    updated repo instead of once per repo.
    """
    start = time.monotonic()

    # --- Machine-readable progress (--progress json) --------------------
    # Silence structlog/stdlib logging and redirect the shared Rich console
    # (used throughout this module and its sibling helpers via `log=console.print`
    # callbacks) to stderr, so stdout carries nothing but the JSON event
    # stream below. Emitter is None in the default 'rich' mode, so every call
    # site below is a no-op there. The redirect is undone when the command's
    # click context closes (success or error), since `console` is a
    # process-wide singleton and leaving it pointed at stderr would bleed
    # into whatever runs next in the same process.
    emitter: JsonProgressEmitter | None = None
    if progress == "json":
        silence_logs_for_machine_output()
        console.file = sys.stderr
        # Restore to None (rather than a captured file object) so `console`
        # goes back to resolving sys.stdout dynamically on each print, its
        # default behavior. Capturing `console.file` here would instead
        # snapshot whatever stdout happens to be at this instant (e.g. a
        # test runner's isolated buffer) and pin the shared console to it.
        click.get_current_context().call_on_close(lambda: setattr(console, "file", None))
        emitter = JsonProgressEmitter()
        emitter.start(repo=str(path or "."), since=since)
    else:
        # Human console mode: quiet library/structlog output so it doesn't
        # interleave with the live progress bar (issue #922); `-v` lets
        # repowise's debug lines through for troubleshooting.
        configure_cli_logging(verbose=verbose)

    # --- Resolve target up front (single repo or workspace) ---
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(console, command="update")

    if target.is_workspace:
        if full:
            raise click.ClickException(
                "--full is single-repo only. Run it inside a specific repo "
                "(or pass --no-workspace / --repo <alias>)."
            )
        try:
            _workspace_update(
                target,
                dry_run=dry_run,
                agents_md=agents_md,
                verbose=verbose,
                docs_flag=docs_flag,
                index_only=index_only,
                since=since,
                provider_name=provider_name,
                model=model,
                reasoning=reasoning,
                cascade_budget=cascade_budget,
                concurrency=concurrency,
                no_cost_tracking=no_cost_tracking,
                progress=progress,
            )
        except Exception as exc:
            if emitter is not None:
                emitter.error(str(exc))
            raise
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=0,
                cost_usd=0.0,
                duration_s=time.monotonic() - start,
                outcome=UpdateOutcome.REGENERATED.value,
            )
        return UpdateOutcome.REGENERATED

    # --- Single-repo path from here on. ---
    repo_path = target.repo_path
    assert repo_path is not None  # single mode always sets repo_path

    # An unindexed linked worktree seeds itself from its base checkout before
    # updating, so post-commit hooks and agents running `update` in a fresh
    # worktree get incremental catch-up instead of a "no previous sync" error.
    # Best-effort: failed validation falls through to the normal flow.
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
            seed_index_from_base(root=repo_path, repo_paths=[repo_path], seed_base=wt_base)

    ensure_repowise_dir(repo_path)

    # If this repo is a workspace member updated here for the first time,
    # inherit the workspace's distill rewrite-hook verdict (best-effort,
    # no-op outside a workspace or once a verdict exists).
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    inherit_workspace_distill_verdict(repo_path)

    # --- Fast -> full upgrade (--full): a distinct path that reuses the
    # persisted graph rather than diffing changed files. Dispatched before any
    # incremental change-detection so the normal `repowise update` flow below
    # is byte-for-byte unchanged. ---
    if full:
        from repowise.cli.commands.upgrade_flow import upgrade_to_full

        if emitter is not None:
            emitter.stage("full_upgrade")
        try:
            upgrade_to_full(
                repo_path,
                provider_name=provider_name,
                model=model,
                reasoning=reasoning,
                concurrency=concurrency,
                yes=yes,
            )
        except Exception as exc:
            if emitter is not None:
                emitter.error(str(exc))
            raise
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=0,
                cost_usd=0.0,
                duration_s=time.monotonic() - start,
                outcome=UpdateOutcome.REGENERATED.value,
            )
        return UpdateOutcome.REGENERATED
    # Truncate the hook-managed log if it has grown past the cap. The hook
    # appends each run unconditionally — without this opportunistic rotation
    # at the start of every CLI update, a busy repo's ``.update.log`` would
    # balloon to MBs over time.
    rotate_update_log_if_needed(repo_path)

    # Load saved API keys from .repowise/.env (won't overwrite existing env vars)
    from repowise.cli.ui import load_dotenv

    load_dotenv(repo_path)
    state = load_state(repo_path)
    resolved_index_only = _resolve_index_only_mode(
        index_only=index_only, docs_flag=docs_flag, state=state
    )
    base_ref = since or (
        state.get("last_sync_commit")
        if resolved_index_only
        else state.get("last_docs_commit", state.get("last_sync_commit"))
    )
    head = get_head_commit(repo_path)

    if base_ref is None:
        # Helpful diagnostic when the user landed here from a workspace
        # directory that was never indexed as a single repo. The auto-detect
        # path normally catches this earlier, but --no-workspace or an
        # explicit path can still route us here.
        hint = ""
        upstream_ws = find_workspace_root(repo_path)
        if upstream_ws is not None:
            hint = (
                f"\nA workspace was detected at {upstream_ws}. "
                "Did you mean: repowise update --workspace?"
            )
        message = (
            f"No previous sync found for {repo_path}. "
            "Run 'repowise init' there first, or pass --since <ref>." + hint
        )
        if emitter is not None:
            emitter.error(message)
        raise click.ClickException(message)

    # A config.yaml / health-rules.json change warrants a full health re-score
    # even when git is unchanged. A missing prior fingerprint is backfilled, not
    # treated as a change. ``config_changed`` gates every "nothing to do" path.
    from repowise.cli.helpers import config_fingerprint

    prev_config_fp = state.get("config_fingerprint")
    curr_config_fp = config_fingerprint(repo_path)
    config_changed = prev_config_fp is not None and prev_config_fp != curr_config_fp

    if head and head == base_ref and not config_changed:
        console.print("[green]Already up to date.[/green]")
        # D7: on a template (index-only) wiki, "up to date" is true of the code
        # but the pages are still unwritten. Point at the command that writes
        # them rather than leaving the user at a dead end, the way `update
        # --docs` used to (it computed base_ref == head and stopped here).
        if resolve_docs_mode(state) == "deterministic":
            console.print(
                "[dim]The wiki is rendered from templates. "
                "Run [bold]repowise generate[/bold] to write pages with a model.[/dim]"
            )
        if prev_config_fp is None and not dry_run:
            save_state(repo_path, {**state, "config_fingerprint": curr_config_fp})
        # Self-heal a row a pre-fix run left behind: state.json can already be
        # current here while the DB head_commit is still the last full index.
        if not dry_run:
            stamp_head_commit(repo_path, head)
            _refresh_editor_stamp(repo_path, agents_md)
            # The index is current, so any pending marker a bailed update left
            # is by definition caught-up (or older); drop it here too rather
            # than waiting for the next content-changing update. This is the
            # quiescent state a stale marker was observed lingering in.
            consume_update_pending(repo_path, head)
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=0,
                cost_usd=0.0,
                duration_s=time.monotonic() - start,
                outcome=UpdateOutcome.NOOP.value,
            )
        return UpdateOutcome.NOOP

    # --- Single-flight lock ---------------------------------------------
    # A live lock from another process means a `repowise update` is already
    # running on this repo. Two updates racing on save_state was the actual
    # root cause of "wiki keeps going stale": post-commit hooks fired during
    # rapid-fire commits would each redo full ingestion + generation from the
    # same outdated base, take 10+ minutes, then save_state out of order so
    # state.json never reflected reality. Bail cleanly instead — and leave
    # the new HEAD in ``.update.pending`` so the running update can roll
    # forward to it at the end of its current pass. Check + acquire are one
    # atomic exclusive create, so two updates arriving together can no
    # longer both pass a separate read check and race anyway.
    existing_lock = try_acquire_update_lock(repo_path, head)
    if existing_lock is not None:
        import time as _time

        elapsed = int(_time.time() - existing_lock.get("started_at", _time.time()))
        target_short = (existing_lock.get("target_commit") or "")[:8]
        write_update_pending(repo_path, head)
        console.print(
            f"[yellow]Another `repowise update` is already running "
            f"(pid {existing_lock.get('pid')}, target {target_short}, "
            f"started {elapsed}s ago).[/yellow]"
        )
        console.print(
            f"[dim]HEAD {head[:8] if head else 'HEAD'} marked as pending; "
            "the running update will roll forward to it. This run regenerated "
            "nothing; watch [bold].repowise/.update.log[/bold] for the one in "
            "flight.[/dim]"
        )
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=0,
                cost_usd=0.0,
                duration_s=time.monotonic() - start,
                outcome=UpdateOutcome.DEFERRED.value,
            )
        return UpdateOutcome.DEFERRED

    # We own the lock from here on: the augment hook suppresses its
    # stale-wiki warning while this run is in flight (typical case: the
    # post-commit hook fires `repowise update` in the background, then a
    # follow-on tool call would otherwise warn that HEAD has moved).
    import atexit

    # Drop the queued marker now that the real lock owns the suppression
    # window. Leaving both behind would cause the augment hook to keep
    # suppressing for the queued-stale-after duration even past a failed run.
    clear_update_queued(repo_path)
    atexit.register(release_update_lock, repo_path)
    atexit.register(clear_update_queued, repo_path)

    # Backfill the docs mode on legacy state files using the same inference
    # the resolver uses, so the post-commit hook and future runs stop relying
    # on the inference. Done before mode resolution and only when no explicit
    # override was passed, so a one-off `--docs` / `--no-docs` doesn't lock
    # anything in.
    explicit_override = (
        # The CLI default of index_only is False; treat it like an
        # override only when the user actually passed the flag, which we
        # can't distinguish here — but the conservative choice is fine:
        # index_only=True is always treated as an override.
        index_only or docs_flag is not None
    )
    if "docs_mode" not in state and not explicit_override:
        state.update(docs_mode_state_fields(resolve_docs_mode(state)))
        save_state(repo_path, state)

    # --- Resolve effective mode (index-only vs full LLM regen) ---
    index_only = resolved_index_only

    # --- Store-format upgrade assessment --------------------------------
    # Single decision point for "does upgrading repowise need to touch this
    # store". Runs any no-LLM auto actions (e.g. re-embed after an embedding
    # model change); reindex is only ever recommended, never forced here.
    # Placed after the single-flight lock so an auto re-embed (a full vector
    # rebuild) can't race a concurrent update. Best-effort: the upgrade layer
    # must never block a routine update.
    try:
        from repowise.cli.upgrade import apply_upgrade, assess_store

        verdict = assess_store(repo_path)
        if not verdict.is_noop:
            if verdict.reindex_recommended and verdict.reindex_command:
                console.print(f"[yellow]Reindex recommended:[/yellow] {verdict.reindex_command}")
                if verdict.user_notice:
                    console.print(f"[dim]{verdict.user_notice}[/dim]")
            if verdict.actions and not dry_run:
                for action in verdict.actions:
                    console.print(f"[dim]Upgrade: {action.reason}[/dim]")
                run_async(apply_upgrade(repo_path, verdict))
        # Surface "what's new" + a PyPI advisory only in an interactive terminal,
        # so the background post-commit hook never spams output. The store's
        # ``written_by`` (read before this run re-stamps it) bounds the panel to
        # releases the user has actually crossed.
        if console.is_terminal and emitter is None:
            _surface_release_news(written_by=verdict.written_by)
    except Exception as exc:  # never block a routine update on the upgrade layer
        log.debug("store_upgrade_skipped", error=str(exc))

    render_header(repo_path, base_ref, head)

    if emitter is not None:
        emitter.stage("detect_changes")

    from repowise.core.ingestion import ChangeDetector

    detector = ChangeDetector(repo_path)
    file_diffs = detector.get_changed_files(base_ref, head or "HEAD")

    if not file_diffs and not config_changed:
        console.print("[green]No changed files detected.[/green]")
        # Always advance the sync pointer so the on-disk freshness marker stays
        # current on no-op syncs. In docs mode, no changed files means no docs
        # work is pending, so the docs pointer can advance to head too, which
        # also heals legacy state that never recorded one.
        persisted = {**state, "last_sync_commit": head, "config_fingerprint": curr_config_fp}
        if not resolved_index_only and head:
            persisted["last_docs_commit"] = head
        save_state(repo_path, persisted)
        # Keep the DB freshness stamp in lockstep with state.json: the server's
        # /repos endpoint reads head_commit from the row, not the state file.
        stamp_head_commit(repo_path, head)
        _refresh_editor_stamp(repo_path, agents_md)
        # We hold the lock and have advanced to head; drop any stale pending
        # marker a bailed update left behind.
        consume_update_pending(repo_path, head)
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=0,
                cost_usd=0.0,
                duration_s=time.monotonic() - start,
                outcome=UpdateOutcome.NOOP.value,
            )
        return UpdateOutcome.NOOP

    if config_changed:
        # Full re-score (not the partial update) so unchanged files pick up the
        # new rules/excludes instead of being left stale.
        console.print("[yellow]Config files changed — re-running health analysis.[/yellow]")
        if dry_run:
            console.print("[yellow]Dry run — health would be re-scored. No changes made.[/yellow]")
            if emitter is not None:
                emitter.done(
                    ok=True,
                    pages_generated=0,
                    cost_usd=0.0,
                    duration_s=time.monotonic() - start,
                    outcome=UpdateOutcome.DRY_RUN.value,
                )
            return UpdateOutcome.DRY_RUN
        cfg = load_config(repo_path)
        exclude_patterns = list(cfg.get("exclude_patterns") or [])
        if emitter is not None:
            emitter.stage("rescore_health")
        try:
            _run_full_health_rescore(repo_path, exclude_patterns, state, head, curr_config_fp)
        except Exception as exc:
            if emitter is not None:
                emitter.error(str(exc))
            raise
        _refresh_editor_stamp(repo_path, agents_md)
        consume_update_pending(repo_path, head)
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=0,
                cost_usd=0.0,
                duration_s=time.monotonic() - start,
                outcome=UpdateOutcome.REGENERATED.value,
            )
        return UpdateOutcome.REGENERATED

    render_changed_files(file_diffs, verbose=verbose)

    # Best-effort steps that fail from here on are collected (not swallowed)
    # and rendered in the completion panel + `--progress json` done event, so
    # "update complete" is only ever claimed when it is actually true.
    degraded: list[str] = []

    # Re-parse changed files and rebuild graph for affected pages
    cfg = load_config(repo_path)

    # Read exclude patterns from config (set during init or via web UI)
    exclude_patterns: list[str] = list(cfg.get("exclude_patterns") or [])

    if emitter is not None:
        emitter.stage("rebuild_graph")

    parsed_files, source_map, graph_builder, repo_structure, file_count, git_meta_map = (
        _rebuild_graph_and_git(
            repo_path,
            file_diffs,
            cfg,
            exclude_patterns,
            git_tier=state.get("git_tier"),
            include_submodules=bool(state.get("include_submodules", False)),
            include_nested_repos=bool(state.get("include_nested_repos", False)),
        )
    )

    if emitter is not None:
        emitter.stage("plan_pages")

    # Determine affected pages (auto-scale budget if not explicitly set)
    if cascade_budget is None:
        from repowise.core.ingestion.change_detector import compute_adaptive_budget

        cascade_budget = compute_adaptive_budget(file_diffs, file_count)
        if verbose:
            console.print(f"Adaptive cascade budget: [cyan]{cascade_budget}[/cyan]")
    # Pass the builder's cached file pagerank so the cascade ordering does
    # not recompute a full-graph pagerank pass on every update.
    affected = detector.get_affected_pages(
        file_diffs,
        graph_builder.graph(),
        cascade_budget,
        pagerank=graph_builder.pagerank(),
    )

    console.print(f"Pages to regenerate: [cyan]{len(affected.regenerate)}[/cyan]")
    if affected.decay_only:
        console.print(f"Pages to decay: [yellow]{len(affected.decay_only)}[/yellow]")

    if dry_run:
        console.print("[yellow]Dry run — no pages regenerated.[/yellow]")
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=0,
                cost_usd=0.0,
                duration_s=time.monotonic() - start,
                outcome=UpdateOutcome.DRY_RUN.value,
            )
        return UpdateOutcome.DRY_RUN

    partial_health_report, dead_code_report = _run_partial_analysis(
        repo_path, graph_builder, git_meta_map, parsed_files, file_diffs
    )

    # Partial health has consumed the per-file ``BlameIndex``; drop it before
    # the metadata reaches persistence / regeneration so the transient,
    # non-serializable object can never leak downstream (mirrors run_pipeline).
    from repowise.core.pipeline.phases.git import drop_transient_git_signals

    drop_transient_git_signals(list(git_meta_map.values()))

    # Refresh the knowledge graph (layers/tour/entry points) when the graph
    # shape changed — previously init-only, so update served a stale
    # orientation snapshot to CLAUDE.md/get_overview forever (#669). None
    # means fingerprint-unchanged: the persisted artifact is still current.
    knowledge_graph_result = _refresh_knowledge_graph(
        repo_path,
        parsed_files,
        graph_builder,
        repo_structure,
        git_meta_map,
        dead_code_report,
        (state.get("knowledge_graph") or {}).get("fingerprint"),
    )

    if index_only:
        # A repo whose wiki was rendered from templates keeps it current here.
        # Re-rendering is free, so the changed files' pages are refreshed on
        # every update rather than frozen at the commit `init` ran on. Repos
        # with no pages (fast mode, or an index from before templates existed)
        # skip this and stay a pure index.
        det_pages: list = []
        index_only_cost = 0.0
        docs_mode = resolve_docs_mode(state)
        if docs_mode == "deterministic":
            from .deterministic import (
                load_prior_page_ids,
                partition_regenerate_by_provenance,
                persist_deterministic_pages,
                regenerate_deterministic_pages,
                regenerate_model_pages,
            )

            if emitter is not None:
                emitter.stage("generate")
            prior_ids = load_prior_page_ids(repo_path)

            # Per-page re-render mode: a page a user upgraded to model prose must
            # not silently revert to a template. Template pages re-render for
            # free; model-written pages stay model-written.
            template_paths, model_paths = partition_regenerate_by_provenance(
                repo_path, affected.regenerate
            )

            det_pages = regenerate_deterministic_pages(
                repo_path=repo_path,
                parsed_files=parsed_files,
                source_map=source_map,
                graph_builder=graph_builder,
                repo_structure=repo_structure,
                git_meta_map=git_meta_map,
                regenerate_paths=template_paths,
                cfg=cfg,
                concurrency=concurrency,
                degraded=degraded,
                dead_code_report=dead_code_report,
                prior_page_ids=prior_ids,
            )

            # Model-written pages: re-render with the model when one resolves
            # (D6 wrote the key into .repowise/.env, so an index-only repo whose
            # pages were upgraded now has a provider). With no provider, keep the
            # existing LLM content and mark it stale — never revert to a template.
            model_pages: list = []
            stale_model_paths: list[str] = []
            if model_paths:
                model_provider = None
                try:
                    from repowise.cli.helpers import resolve_provider

                    model_provider = resolve_provider(provider_name, model, repo_path=repo_path)
                except Exception:
                    model_provider = None

                if model_provider is not None:
                    from repowise.cli.providers import build_cost_tracker

                    tracker = build_cost_tracker(
                        repo_path, repo_path.name, no_cost_tracking=no_cost_tracking
                    )
                    model_provider._cost_tracker = tracker
                    model_pages = regenerate_model_pages(
                        repo_path=repo_path,
                        parsed_files=parsed_files,
                        source_map=source_map,
                        graph_builder=graph_builder,
                        repo_structure=repo_structure,
                        git_meta_map=git_meta_map,
                        regenerate_paths=model_paths,
                        cfg=cfg,
                        concurrency=concurrency,
                        degraded=degraded,
                        provider=model_provider,
                        dead_code_report=dead_code_report,
                        prior_page_ids=prior_ids,
                    )
                    index_only_cost = float(getattr(tracker, "session_cost", 0.0) or 0.0)
                    # Any requested model page the render did not actually
                    # produce is kept and marked stale. generate_all catches a
                    # per-page provider error and filters that page out of its
                    # result, so a partial batch (page A rendered, page B errored)
                    # must not leave B silently fresh-but-outdated — that is the
                    # exact revert-in-disguise Task 3 prevents. Full failure
                    # (empty result) falls out of the same computation.
                    rendered_paths = {
                        pg.page_id.split("file_page:", 1)[1]
                        for pg in model_pages
                        if getattr(pg, "page_id", "").startswith("file_page:")
                    }
                    stale_model_paths = [p for p in model_paths if p not in rendered_paths]
                else:
                    stale_model_paths = list(model_paths)
                    console.print(
                        "  [yellow]![/yellow] "
                        f"{len(model_paths)} model-written page(s) changed but no "
                        "provider is configured; keeping them and marking stale. "
                        "Add a key, then run [bold]repowise generate[/bold]."
                    )

            all_pages = det_pages + model_pages
            if all_pages or stale_model_paths:
                state["total_pages"] = persist_deterministic_pages(
                    repo_path=repo_path,
                    generated_pages=all_pages,
                    # decay_only are cascade-reached templates; the kept model
                    # pages are stale until a provider re-writes them.
                    decay_paths=[*affected.decay_only, *stale_model_paths],
                    degraded=degraded,
                )
                # Advance the docs commit only when something was actually
                # re-rendered to HEAD; a run that only kept-and-staled model pages
                # has not brought the wiki up to HEAD.
                if all_pages:
                    state["last_docs_commit"] = head
                if det_pages:
                    console.print(
                        f"  [green]✓[/green] Re-rendered [bold]{len(det_pages)}[/bold] "
                        "wiki pages from structure"
                    )
                if model_pages:
                    console.print(
                        f"  [green]✓[/green] Re-wrote [bold]{len(model_pages)}[/bold] "
                        "upgraded page(s) with the model"
                    )
            det_pages = all_pages
        if emitter is not None:
            emitter.stage("persist")
        try:
            _persist_index_only_update(
                repo_path,
                graph_builder,
                git_meta_map,
                dead_code_report,
                partial_health_report,
                state,
                head,
                start,
                [fd.path for fd in file_diffs],
                file_diffs=file_diffs,
                knowledge_graph_result=knowledge_graph_result,
                parsed_files=parsed_files,
                degraded=degraded,
                # The repo's docs mode, not this run's page count: a commit
                # that touched no source file re-renders nothing, and that is
                # not the same as having no wiki.
                template_wiki=docs_mode == "deterministic",
                pages_rendered=len(det_pages),
            )
        except Exception as exc:
            if emitter is not None:
                emitter.error(str(exc))
            raise
        _refresh_editor_stamp(repo_path, agents_md, degraded)
        # Index-only is the post-commit hook's hot path; clear any stale pending
        # marker here too, not just on the full-docs path.
        consume_update_pending(repo_path, head)
        _record_update_outcome(
            index_only=True,
            changed_count=len(file_diffs),
            generated_pages=det_pages,
            docs_mode=docs_mode,
        )
        if emitter is not None:
            emitter.done(
                ok=True,
                pages_generated=len(det_pages),
                cost_usd=index_only_cost,
                duration_s=time.monotonic() - start,
                degraded=degraded,
                outcome=UpdateOutcome.REGENERATED.value,
            )
        return UpdateOutcome.REGENERATED

    # The generation/LLM layer is only needed past this point — importing it
    # above the index-only branch would make every index-only update (the
    # post-commit hook's hot path) pay the import for code it never runs.
    from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator

    language = cfg.get("language", "en")
    # Config-driven (saved by `repowise init`); CLI override not surfaced
    # on update yet — defaults to on to keep the onboarding collection
    # fresh as the codebase evolves.
    enable_onboarding_cfg = bool(cfg.get("enable_onboarding", True))
    # Honor the tiering knobs chosen at init so update regenerates with the same
    # coverage. Without reading these back, every update would silently drop the
    # deterministic tail (and any tier-1 cap) to their defaults.
    tail_dirs_cfg = cfg.get("tier2_tail_dirs")
    config = GenerationConfig.from_repo_config(
        cfg,
        max_concurrency=concurrency,
        language=language,
        reasoning=resolve_reasoning(reasoning, cfg),
        enable_onboarding=enable_onboarding_cfg,
        # Honor the wiki style chosen at init (or via `repowise restyle`) so pages
        # regenerated for changed files match the rest of the wiki's voice.
        wiki_style=cfg.get("wiki_style", "comprehensive"),
        tier1_top_n=cfg.get("tier1_top_n"),
        tier2_tail_enabled=bool(cfg.get("tier2_tail_enabled", True)),
        tier2_tail_cap=cfg.get("tier2_tail_cap"),
        tier2_tail_dirs=tuple(tail_dirs_cfg) if tail_dirs_cfg else None,
        # An incremental update regenerates only the changed files' pages, and
        # it feeds generate_all a parsed_files filtered to those files. Levels 3
        # and up describe the whole repository from parsed_files, so without
        # this a one-commit update would rewrite the codebase map, module and
        # overview pages from a truncated view (a module page claiming one file,
        # a codebase map with no directories). Stop the ladder after level 2 and
        # leave the repo-wide pages for a full run — the same guard the
        # deterministic index-only update already uses. `repowise generate`
        # refreshes the repo-wide pages correctly, from the complete view.
        file_pages_only=True,
    )

    # Resolve the generation provider. If the repo wants docs but was never
    # given a provider/key (e.g. it started --index-only and later asked for
    # LLM docs, or `repowise update --docs`), onboard the same way init
    # does: prompt for provider + key and persist, but only in an interactive
    # terminal. Hooks / CI / --progress json keep the clean non-blocking error.
    # In the workspace docs flow each member reaches this per-repo run; the
    # first prompt sets the key in-process, so later members auto-resolve.
    provider = resolve_provider_or_prompt(
        provider_name,
        model,
        repo_path,
        reasoning=reasoning,
        interactive=_docs_provider_prompt_allowed(emitter),
    )

    # Attach a DB-backed CostTracker so every LLM call made during this update
    # (decision rescan + page regeneration) is persisted to the `llm_costs`
    # table — matching what `repowise init` already does. Without this,
    # `repowise costs` only ever reflects the initial index and shows $0 for
    # all subsequent updates.
    from repowise.cli.providers import build_cost_tracker

    cost_tracker = build_cost_tracker(repo_path, repo_path.name, no_cost_tracking=no_cost_tracking)
    provider._cost_tracker = cost_tracker

    # (dead_code_report computed above, before the index-only branch)

    # Re-scan changed files for inline decision markers
    new_decision_markers: list = []
    try:
        from repowise.core.analysis.decision_extractor import DecisionExtractor

        changed_paths = [fd.path for fd in file_diffs if fd.status in ("added", "modified")]
        if changed_paths:
            extractor = DecisionExtractor(
                repo_path=repo_path,
                provider=provider,
                graph=graph_builder.graph(),
                git_meta_map=git_meta_map,
            )
            # Label these calls as decision extraction so the Costs page can
            # tell them apart from page regeneration (both ride this provider).
            with cost_tracker.record_as("decision_extraction"):
                new_decision_markers = run_async(
                    extractor.scan_inline_markers(restrict_to_files=changed_paths)
                )
            if new_decision_markers and verbose:
                console.print(
                    f"New decision markers found: [green]{len(new_decision_markers)}[/green]"
                )
    except Exception as exc:
        degraded.append(f"Decision re-scan: {exc}")
        if verbose:
            console.print(f"[yellow]Decision re-scan skipped: {exc}[/yellow]")

    # Session-sourced decisions: mine agent transcript lines appended since
    # the last update, structure new candidates in one batched LLM pass, and
    # collect the observation-qualified promotions. They ride the same
    # decision upsert as the marker re-scan below. Everything stays local;
    # `decisions.session_mining: false` in .repowise/config.yaml disables it.
    session_decisions: list = []
    try:
        from repowise.core.sessions.miners.decisions import (
            mine_session_decisions,
            session_mining_enabled,
        )

        if session_mining_enabled(cfg):
            session_decisions = run_async(mine_session_decisions(repo_path, provider=provider))
            if session_decisions and verbose:
                promoted_titles = {d.title for d in session_decisions}
                console.print(f"Session decisions promoted: [green]{len(promoted_titles)}[/green]")
    except Exception as exc:
        degraded.append(f"Session decision mining: {exc}")
        if verbose:
            console.print(f"[yellow]Session decision mining skipped: {exc}[/yellow]")

    # Usage feedback v1: decisions the augment hooks injected into agent
    # sessions are judged against those sessions' mined corrections (followed
    # -> staleness relaxes, contradicted -> staleness bumps). Pure SQLite over
    # the staging sidecar + decision_records; no LLM.
    try:
        from repowise.core.sessions.miners.decisions import apply_injection_feedback

        if session_mining_enabled(cfg):

            async def _run_injection_feedback() -> dict:
                from repowise.cli.helpers import get_db_url_for_repo
                from repowise.core.persistence import (
                    create_engine,
                    create_session_factory,
                    get_session,
                    init_db,
                    upsert_repository,
                )

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                await init_db(engine)
                sf = create_session_factory(engine)
                async with get_session(sf) as session:
                    repo = await upsert_repository(
                        session, name=repo_path.name, local_path=str(repo_path)
                    )
                    res = await apply_injection_feedback(session, repo.id, repo_path)
                await engine.dispose()
                return res

            feedback = run_async(_run_injection_feedback())
            if verbose and (feedback.get("followed") or feedback.get("contradicted")):
                console.print(
                    f"Injected-decision feedback: [green]{feedback.get('followed', 0)} "
                    f"followed[/green], [yellow]{feedback.get('contradicted', 0)} "
                    "contradicted[/yellow]"
                )
    except Exception as exc:
        degraded.append(f"Injection feedback: {exc}")
        if verbose:
            console.print(f"[yellow]Injection feedback skipped: {exc}[/yellow]")

    # Count of decision records touched by evolution, surfaced in the panel.
    decisions_evolved = 0

    # Build the shared vector store once: reused by the supersession detector
    # below and by the decision upsert in _persist (semantic dedup + search).
    decision_vector_store = _build_update_vector_store(repo_path, cfg)

    # --- Two-pass decision evolution (Phase 3C), BEFORE page regeneration ----
    # Cross-reference the diff + new commit bodies against existing decisions
    # governing the changed files (Pass 1, cheap) then ask the LLM whether each
    # survivor is amended/superseded/reaffirmed (Pass 2, gated). Governed pages
    # of an evolved decision are folded into ``affected.regenerate`` so they
    # re-render in this same run — the cascade-coupling the design calls for.
    try:
        import json as _json_evo

        evidence_by_file: dict[str, str] = {}
        for fd in file_diffs:
            if fd.status not in ("added", "modified"):
                continue
            parts: list[str] = []
            if fd.trigger_commit_message:
                parts.append(fd.trigger_commit_message)
            if fd.diff_text:
                parts.append(fd.diff_text)
            meta = git_meta_map.get(fd.path)
            if meta:
                for c in _json_evo.loads(meta.get("significant_commits_json", "[]") or "[]"):
                    msg = c.get("message") or ""
                    body = c.get("body") or ""
                    if msg or body:
                        parts.append(f"{msg}\n{body}")
            if parts:
                evidence_by_file[fd.path] = "\n\n".join(parts)

        changed_set = {fd.path for fd in file_diffs if fd.status in ("added", "modified")}
        if changed_set:
            from repowise.core.analysis.decision_evolution import run_update_evolution

            async def _run_evolution() -> dict:
                from repowise.cli.helpers import get_db_url_for_repo
                from repowise.core.persistence import (
                    create_engine,
                    create_session_factory,
                    get_session,
                    init_db,
                    upsert_repository,
                )

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                await init_db(engine)
                sf = create_session_factory(engine)
                async with get_session(sf) as session:
                    repo = await upsert_repository(
                        session, name=repo_path.name, local_path=str(repo_path)
                    )
                    res = await run_update_evolution(
                        session,
                        repo.id,
                        changed_files=changed_set,
                        evidence_by_file=evidence_by_file,
                        provider=provider,
                    )
                await engine.dispose()
                return res

            evo = run_async(_run_evolution())
            decisions_evolved = (
                evo.get("superseded", 0) + evo.get("amended", 0) + evo.get("reaffirmed", 0)
            )
            evo_regen = evo.get("regen_files") or set()
            if evo_regen:
                # page_id == file path for file pages, so adding the governed
                # file paths schedules their pages for regeneration.
                affected.regenerate = list(
                    dict.fromkeys([*affected.regenerate, *sorted(evo_regen)])
                )
                if verbose:
                    console.print(
                        f"Decision evolution: [cyan]{evo.get('superseded', 0)} superseded[/cyan], "
                        f"[cyan]{evo.get('amended', 0)} amended[/cyan], "
                        f"[green]{evo.get('reaffirmed', 0)} reaffirmed[/green]; "
                        f"+{len(evo_regen)} governed page(s) queued for regen."
                    )
    except Exception as exc:
        degraded.append(f"Decision evolution: {exc}")
        if verbose:
            console.print(f"[yellow]Decision evolution skipped: {exc}[/yellow]")

    # Filter to only affected files
    regen_set = set(affected.regenerate)
    affected_parsed = [pf for pf in parsed_files if pf.file_info.path in regen_set]
    affected_source = {p: s for p, s in source_map.items() if p in regen_set}

    # Load prior pages so the generator can skip the LLM call for any affected
    # file whose freshly rendered prompt still hashes to the persisted value.
    async def _load_prior() -> dict[str, object]:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            load_prior_pages,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            return await load_prior_pages(session, repo.id)

    try:
        prior_pages = run_async(_load_prior())
    except Exception as exc:
        # Without prior pages the prompt-hash skip is off and every affected
        # page re-bills; surface that instead of silently paying it.
        degraded.append(f"Prior-page reuse: {exc}")
        prior_pages = {}

    # Generate affected pages. The vector store (shared with the decision
    # pass above) must ride along: without it regenerated pages are never
    # re-embedded, so every update silently evicted its pages from semantic
    # search — on long-lived repos the LanceDB corpus ended up holding
    # decisions and structural pages but zero current file pages.
    assembler = ContextAssembler(config)
    generator = PageGenerator(
        provider,
        assembler,
        config,
        vector_store=decision_vector_store,
        language=config.language,
        prior_pages=prior_pages,
        repo_path=repo_path,
    )
    repo_name = repo_path.name

    if emitter is not None:
        emitter.stage("generate")

    # Drive a live progress bar (owl spinner + running cost) off generate_all's
    # page callbacks, matching init's generation phase. Cost is read from the
    # tracker as each page lands so the readout ticks up in real time. In
    # json progress mode there is no bar, so the same callbacks emit
    # total_known / page_done events on stdout instead.
    from contextlib import nullcontext

    completed_pages = 0
    total_pages: int | None = None

    def _on_total_known(total: int) -> None:
        nonlocal total_pages
        total_pages = total
        if emitter is not None:
            emitter.total_known(total)
        else:
            gen_progress.update(gen_task, total=total)

    def _on_page_done(_page_id: str) -> None:
        nonlocal completed_pages, total_pages
        completed_pages += 1
        # The total is an up-front estimate (see _announce_total); some page
        # categories can't be predicted exactly, so keep it monotonic with the
        # real count, so it never renders "43 of 41" (issue #922).
        if total_pages is not None and completed_pages > total_pages:
            total_pages = completed_pages
        if emitter is not None:
            emitter.page_done(
                completed=completed_pages, total=total_pages, cost_usd=cost_tracker.session_cost
            )
        else:
            gen_progress.update(
                gen_task, advance=1, total=total_pages, cost=cost_tracker.session_cost
            )

    # Checkpoint each page to the DB as it lands: a crash mid-generation used
    # to lose every finished page (persist ran only at the very end), so the
    # rerun re-billed all of them. With the row persisted, the rerun's
    # prompt-hash skip sees the fresh content and never re-calls the LLM.
    from .persistence import PageCheckpointer

    checkpointer = PageCheckpointer(repo_path, repo_name)

    async def _generate_with_checkpoint() -> list:
        await checkpointer.start()
        try:
            return await generator.generate_all(
                affected_parsed,
                affected_source,
                graph_builder,
                repo_structure,
                repo_name,
                on_page_done=_on_page_done,
                on_total_known=_on_total_known,
                git_meta_map=git_meta_map,
                repo_path=repo_path,
                on_page_ready=checkpointer.on_page_ready,
            )
        finally:
            await checkpointer.close()

    with make_generation_progress() if emitter is None else nullcontext() as gen_progress:
        gen_task = (
            gen_progress.add_task("Generating pages...", total=None, cost=0.0)
            if gen_progress is not None
            else None
        )

        try:
            generated_pages = run_async(_generate_with_checkpoint())
        except Exception as exc:
            if emitter is not None:
                emitter.error(str(exc))
            raise

        # Reconcile the bar to the pages actually produced. The up-front total
        # is an estimate and onboarding slots may gate-skip, leaving it above
        # the real count; snap total to completed so the bar reads N of N at
        # 100% instead of stalling short (issue #922).
        if gen_task is not None:
            gen_progress.update(gen_task, total=completed_pages, completed=completed_pages)

    # Surface the FAQ-weighted budget tilt when session demand shaped this run
    # (silent when there is no history to weight; human console mode only).
    if emitter is None and getattr(generator, "faq_demand_summary", None):
        console.print(f"[dim]{generator.faq_demand_summary}[/dim]")

    if checkpointer.failure:
        degraded.append(f"Per-page crash checkpointing: {checkpointer.failure}")

    # Flush the buffered LLM cost rows now that generation is done — a single
    # transaction outside the contended generation window (issue #326).
    from repowise.cli.providers import flush_cost_tracker

    flush_cost_tracker(cost_tracker)

    # LLM re-enrichment of the refreshed KG (layer naming + summary backfill
    # from this run's regenerated pages), mirroring the init pipeline. Only
    # runs when the graph shape changed — carry-forward already preserved the
    # prior names, so an unchanged KG never pays an enrichment call.
    if knowledge_graph_result is not None:
        try:
            from repowise.core.generation.knowledge_graph import enrich_knowledge_graph

            knowledge_graph_result = run_async(
                enrich_knowledge_graph(
                    kg_skeleton=knowledge_graph_result,
                    llm_client=provider,
                    graph_builder=graph_builder,
                    repo_structure=repo_structure,
                    tech_stack=knowledge_graph_result.project.get("tech_stack", []),
                    generated_pages=generated_pages,
                    reasoning=config.reasoning,
                )
            )
        except Exception as exc:
            console.print(f"[yellow]Knowledge-graph enrichment skipped: {exc}[/yellow]")
            degraded.append(f"Knowledge-graph enrichment: {exc}")

    # Persist everything in one transaction (pages fail loudly, derived
    # layers degrade into the collected list) — see _persist_full_update.
    from .persistence import _persist_full_update

    if emitter is not None:
        emitter.stage("persist")
    try:
        db_total_pages = _persist_full_update(
            repo_path=repo_path,
            repo_name=repo_name,
            generated_pages=generated_pages,
            file_diffs=file_diffs,
            git_meta_map=git_meta_map,
            new_decision_markers=[*new_decision_markers, *session_decisions],
            decision_vector_store=decision_vector_store,
            provider=provider,
            partial_health_report=partial_health_report,
            dead_code_report=dead_code_report,
            graph_builder=graph_builder,
            knowledge_graph_result=knowledge_graph_result,
            degraded=degraded,
            decay_paths=affected.decay_only,
            parsed_files=parsed_files,
        )
    except Exception as exc:
        if emitter is not None:
            emitter.error(str(exc))
        raise

    # ---- Editor project files (best-effort) ----
    _refresh_editor_stamp(repo_path, agents_md, degraded)

    # Update state
    from repowise.cli.helpers import config_fingerprint

    if knowledge_graph_result is not None:
        try:
            from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json

            save_knowledge_graph_json(repo_path, knowledge_graph_result)
            state["knowledge_graph"] = build_kg_state(knowledge_graph_result)
        except Exception as exc:
            console.print(f"[yellow]Knowledge-graph export skipped: {exc}[/yellow]")
            degraded.append(f"Knowledge-graph export: {exc}")

    state["last_sync_commit"] = head
    state["last_docs_commit"] = head
    # Real DB total, not an accumulation: regeneration upserts existing pages,
    # so adding len(generated_pages) every run inflated the count forever.
    state["total_pages"] = db_total_pages
    state["config_fingerprint"] = config_fingerprint(repo_path)
    save_state(repo_path, state)

    # --- Pending-marker cleanup --------------------------------------------
    # Another `repowise update` (from a post-commit hook) may have written a
    # ``.update.pending`` marker while we were generating pages. Drop it now
    # that we've caught up; it is only kept when it points to a commit strictly
    # newer than the one we just indexed.
    consume_update_pending(repo_path, head)

    # Trigger cross-repo hooks if this repo is part of a workspace. The
    # workspace docs flow suppresses this per-repo and runs the hooks once
    # over every updated repo, so the cross-repo layer isn't rebuilt from a
    # half-updated set on every member.
    try:
        ws_root = find_workspace_root(repo_path) if not skip_cross_repo_hooks else None
        if ws_root is not None:
            from repowise.core.workspace import WorkspaceConfig
            from repowise.core.workspace.update import run_cross_repo_hooks

            ws_config = WorkspaceConfig.load(ws_root)
            # Find this repo's alias in the workspace config

            repo_abs = repo_path.resolve()
            alias = None
            for entry in ws_config.repos:
                if (ws_root / entry.path).resolve() == repo_abs:
                    alias = entry.alias
                    break
            if alias and len(ws_config.repos) >= 2:
                console.print("Running cross-repo analysis...")
                run_async(run_cross_repo_hooks(ws_config, ws_root, [alias]))
                console.print("[green]Cross-repo analysis updated.[/green]")
    except Exception as exc:
        # Cross-repo hooks must never fail the update, but a stale cross-repo
        # layer should not masquerade as a fully clean run.
        degraded.append(f"Cross-repo analysis: {exc}")

    elapsed = time.monotonic() - start
    _record_update_outcome(
        index_only=False,
        changed_count=len(file_diffs),
        provider=provider,
        generated_pages=generated_pages,
    )
    if emitter is not None:
        emitter.done(
            ok=True,
            pages_generated=len(generated_pages),
            cost_usd=cost_tracker.session_cost,
            duration_s=elapsed,
            degraded=degraded,
            outcome=UpdateOutcome.REGENERATED.value,
        )
        return UpdateOutcome.REGENERATED
    show_full_completion(
        generated_pages=generated_pages,
        decay_count=len(affected.decay_only),
        decisions_changed=len(new_decision_markers) + len(session_decisions) + decisions_evolved,
        provider=provider,
        cost=cost_tracker.session_cost,
        tokens=cost_tracker.session_tokens,
        elapsed=elapsed,
        degraded=degraded,
    )
    if verbose:
        _render_update_report(generated_pages, affected, new_decision_markers, elapsed)
    return UpdateOutcome.REGENERATED

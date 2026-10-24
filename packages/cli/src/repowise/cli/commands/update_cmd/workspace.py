"""``repowise update`` workspace flow \u2014 update stale repos in a workspace."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import CommandTarget, console, run_async


def _print_repo_result(result: Any) -> None:
    """Print one repo's outcome. Shared by both workspace passes.

    The index-only pass and the docs pass report identically, and keeping two
    copies of this is what let the deferral line drift into saying only that
    something else was running, without saying for how long.
    """
    from repowise.core.update_lock import UPDATE_LOCK_SUSPECT_AFTER_SECONDS, format_lock_age

    if result.error:
        console.print(f"    [red]✗ {result.alias}: {result.error}[/red]")
    elif result.skipped_reason == "in_flight":
        # Surface skipped-because-in-flight as a yellow note rather than a
        # silent skip. Single-flight is the noise-fix path, so the user
        # benefits from seeing it actually trigger — and the age is what tells
        # a normal overlap apart from an update that has stopped progressing.
        age = result.lock_age_seconds
        held = format_lock_age(age)
        note = (
            ""
            if age is None or age <= UPDATE_LOCK_SUSPECT_AFTER_SECONDS
            else " That is long enough to be worth checking on."
        )
        console.print(
            f"    [yellow]↻ {result.alias}: another update has held the lock "
            f"{held}; this commit was queued for it to pick up.{note}[/yellow]"
        )
    elif result.updated:
        console.print(
            f"    [green]✓[/green] {result.alias}: "
            f"{result.file_count} files, {result.symbol_count:,} symbols"
        )


def _workspace_update(
    target: CommandTarget,
    *,
    dry_run: bool = False,
    agents_md: bool | None = None,
    verbose: bool = False,
    docs_flag: bool | None = None,
    index_only: bool = False,
    since: str | None = None,
    provider_name: str | None = None,
    model: str | None = None,
    reasoning: str | None = None,
    cascade_budget: int | None = None,
    concurrency: int = 10,
    no_cost_tracking: bool = False,
    progress: str = "rich",
    include_working_tree: bool = False,
) -> None:
    """Update stale repos in a workspace.

    Takes a resolved :class:`CommandTarget` so the caller has full control
    over how the workspace was located (auto-detected vs explicit flag).

    Each stale repo resolves its own docs-vs-index-only mode the same way a
    single-repo update does, from its persisted docs mode plus any
    ``--docs`` / ``--no-docs`` / ``--index-only`` override passed here. Repos
    that want docs are updated through the full single-repo docs path (so
    their wiki pages, diagrams, and decisions stay as current as they would
    under ``repowise update`` run inside the repo); the rest take the fast
    parallel index-only path. See :func:`_workspace_docs_update`.
    """
    from repowise.core.workspace.update import sync_workspace_state_from_disk

    start = time.monotonic()
    started_at = datetime.now(UTC)
    ws_root = target.ws_root
    ws_config = target.ws_config
    repo_alias = target.repo_filter
    if ws_root is None or ws_config is None:
        # Defensive: callers should always pass a workspace-mode target,
        # but guard against misuse so the error message is clear.
        raise click.ClickException("_workspace_update called without a workspace target.")

    console.print(f"[bold]repowise update[/bold]: workspace {ws_root.name}")
    console.print()

    # Staleness reads the workspace config, and an "up to date" repo has its DB
    # row stamped from live HEAD below. Read each repo's state.json first, as
    # update_workspace does, so a config ahead of state.json cannot stamp the
    # row past the commit state.json names.
    sync_workspace_state_from_disk(ws_root, ws_config)
    scan = _scan_workspace(
        ws_root,
        ws_config,
        repo_alias,
        index_only=index_only,
        docs_flag=docs_flag,
        include_working_tree=include_working_tree,
        verbose=verbose,
    )
    if not dry_run and scan.up_to_date_repos:
        _reconcile_up_to_date(ws_root, scan.up_to_date_repos)

    if scan.stale_count == 0:
        console.print("[green]All repos are up to date.[/green]")
        if not dry_run:
            _rerun_analysis_if_config_changed(ws_root, ws_config, started_at)
            _refresh_workspace_editor_project_files(
                ws_root=ws_root,
                ws_config=ws_config,
                repo_filter=repo_alias,
                agents_md=agents_md,
            )
        return

    if dry_run:
        console.print(f"[yellow]Dry run: {scan.stale_count} repo(s) would be updated.[/yellow]")
        return

    # Repos that want docs take the full single-repo docs path; an all
    # index-only workspace stays on the fast parallel path below.
    if scan.docs_aliases:
        _workspace_docs_update(
            ws_root=ws_root,
            ws_config=ws_config,
            repo_filter=repo_alias,
            docs_aliases=scan.docs_aliases,
            start=start,
            started_at=started_at,
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
            include_working_tree=include_working_tree,
        )
        return
    _workspace_index_only_update(
        ws_root,
        ws_config,
        repo_alias,
        start=start,
        started_at=started_at,
        agents_md=agents_md,
        include_working_tree=include_working_tree,
    )


def _rerun_analysis_if_config_changed(ws_root: Path, ws_config: Any, started_at: datetime) -> None:
    """Re-run the cross-repo analysis when only its settings changed.

    An edited ``contracts:`` or ``conformance:`` block moves no repo, so the
    staleness scan alone would leave the edit without effect until some repo
    changed. Settings that only affect matching re-link the stored contracts
    without reading any repo.
    """
    from repowise.core.workspace.update import analysis_config_changed, run_cross_repo_hooks

    if not analysis_config_changed(ws_config, ws_root):
        return
    console.print("Workspace settings changed: re-running cross-repo analysis...")
    try:
        run_async(run_cross_repo_hooks(ws_config, ws_root, []))
    except Exception as exc:
        # The editor files still refresh; the next update retries the analysis.
        console.print(f"[yellow]Cross-repo analysis failed: {exc}[/yellow]")
        return
    console.print("[green]Cross-repo analysis updated.[/green]")
    _print_breaking_changes(ws_root, started_at)


@dataclass
class _WorkspaceScan:
    """Which workspace repos need an update, and how."""

    stale_count: int = 0
    up_to_date_count: int = 0
    #: (path, HEAD) of indexed, up-to-date repos whose DB stamp is reconciled.
    up_to_date_repos: list[tuple[Path, str]] = field(default_factory=list)
    #: Stale, indexed repos whose effective mode is docs. Everything else stale,
    #: never-indexed repos included, takes the fast index-only path.
    docs_aliases: set[str] = field(default_factory=set)


@dataclass
class _RepoStaleness:
    indexed: bool
    is_stale: bool
    head: str | None
    status: str
    state: dict[str, Any]


def _scan_workspace(
    ws_root: Path,
    ws_config: Any,
    repo_alias: str | None,
    *,
    index_only: bool,
    docs_flag: bool | None,
    include_working_tree: bool,
    verbose: bool,
) -> _WorkspaceScan:
    """Print each repo's staleness and sort the stale ones into docs and index-only."""
    scan = _WorkspaceScan()
    for entry in ws_config.repos:
        if repo_alias and entry.alias != repo_alias:
            continue
        abs_path = (ws_root / entry.path).resolve()
        repo = _repo_staleness(abs_path, entry.last_commit_at_index, include_working_tree)
        if repo.is_stale:
            scan.stale_count += 1
            if repo.indexed and _wants_docs_update(repo.state, index_only, docs_flag):
                scan.docs_aliases.add(entry.alias)
        # Up-to-date repos collapse into one count line unless -v lists them.
        if repo.indexed and not repo.is_stale:
            scan.up_to_date_count += 1
            if repo.head:
                scan.up_to_date_repos.append((abs_path, repo.head))
            if not verbose:
                continue
        console.print(f"  {entry.alias:<20} {repo.status}")

    if scan.up_to_date_count and not verbose:
        console.print(f"  [dim]{scan.up_to_date_count} repo(s) up to date[/dim]")
    console.print()
    return scan


def _repo_staleness(
    abs_path: Path, stored: str | None, include_working_tree: bool
) -> _RepoStaleness:
    """Whether one repo is behind its index, its config or its working tree."""
    from repowise.cli.helpers import load_state
    from repowise.core.ingestion.change_detector import has_working_tree_changes
    from repowise.core.repo_config import config_fingerprint
    from repowise.core.workspace import check_repo_staleness

    is_stale, head, behind = check_repo_staleness(abs_path, stored)
    indexed = (abs_path / ".repowise").is_dir()
    repo_state = load_state(abs_path) if indexed else {}
    config_stale = bool(
        indexed
        and (
            repo_state.get("config_fingerprint") is None
            or repo_state.get("config_fingerprint") != config_fingerprint(abs_path)
        )
    )
    is_stale = is_stale or config_stale
    dirty = bool(
        indexed and not is_stale and include_working_tree and has_working_tree_changes(abs_path)
    )
    is_stale = is_stale or dirty
    if not indexed:
        status = "[dim]not indexed[/dim]"
    elif config_stale and not behind:
        status = "[yellow]config changed[/yellow]"
    elif dirty:
        status = "[yellow]uncommitted changes[/yellow]"
    elif is_stale:
        status = f"[yellow]{behind} new commit(s)[/yellow]"
    else:
        status = "[green]up to date[/green]"
    return _RepoStaleness(indexed, is_stale, head, status, repo_state)


def _wants_docs_update(
    repo_state: dict[str, Any], index_only: bool, docs_flag: bool | None
) -> bool:
    """Whether a stale, indexed repo takes the single-repo docs path."""
    from repowise.core.docs_mode import resolve_docs_mode

    from .mode import _resolve_index_only_mode

    if not _resolve_index_only_mode(index_only=index_only, docs_flag=docs_flag, state=repo_state):
        return True
    # A template wiki re-renders the changed files' pages for free on the
    # single-repo path; the core index-only path would freeze it. Model-written
    # pages a user upgraded stay put.
    return resolve_docs_mode(repo_state) == "deterministic"


def _reconcile_up_to_date(ws_root: Path, up_to_date_repos: list[tuple[Path, str]]) -> None:
    """Reconcile the DB freshness stamp of up-to-date repos.

    Staleness is measured against the workspace config, but the server and the
    MCP freshness check read each repo DB's head_commit, which the all-up-to-date
    path would otherwise never touch. A repo nothing has moved since the last
    refresh (see :mod:`.member_refresh`) already carries today's stamp, so only
    the others are opened.
    """
    from repowise.core.workspace import reconcile_repo_head_commit

    from .member_refresh import is_unchanged, load_member_stamps

    stamps = load_member_stamps(ws_root)
    moved = [(path, head) for path, head in up_to_date_repos if not is_unchanged(path, stamps)]
    if not moved:
        return

    async def _reconcile() -> None:
        for repo_path, head in moved:
            await reconcile_repo_head_commit(repo_path, head)

    run_async(_reconcile())


def _workspace_index_only_update(
    ws_root: Path,
    ws_config: Any,
    repo_alias: str | None,
    *,
    start: float,
    started_at: datetime,
    agents_md: bool | None,
    include_working_tree: bool,
) -> None:
    """Update every stale repo on the fast parallel index-only path."""
    from repowise.core.workspace import update_workspace

    def _on_start(alias: str) -> None:
        console.print(f"  Updating [bold]{alias}[/bold]...")

    results = run_async(
        update_workspace(
            ws_root,
            ws_config,
            repo_filter=repo_alias,
            dry_run=False,
            include_working_tree=include_working_tree,
            # _reconcile_up_to_date restamped the members that moved.
            restamp_up_to_date=False,
            on_repo_start=_on_start,
            on_repo_done=_print_repo_result,
        )
    )

    # Members indexed for the first time (e.g. added with --no-index) would
    # otherwise default the distill rewrite hook to enabled despite a
    # workspace-wide decline at init.
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    for entry in ws_config.repos:
        if repo_alias and entry.alias != repo_alias:
            continue
        inherit_workspace_distill_verdict((ws_root / entry.path).resolve())

    updated = sum(1 for r in results if r.updated)
    errors = sum(1 for r in results if r.error)
    skipped = sum(1 for r in results if r.skipped_reason)
    total_files = sum(r.file_count for r in results if r.updated)
    total_symbols = sum(r.symbol_count for r in results if r.updated)

    _refresh_workspace_editor_project_files(
        ws_root=ws_root,
        ws_config=ws_config,
        repo_filter=repo_alias,
        agents_md=agents_md,
    )

    from .reporting import show_workspace_completion

    _print_breaking_changes(ws_root, started_at)

    show_workspace_completion(
        ws_name=ws_root.name,
        updated=updated,
        skipped=skipped,
        errors=errors,
        total_files=total_files,
        total_symbols=total_symbols,
        elapsed=time.monotonic() - start,
    )


_BREAKING_DETAIL_LINES = 3


def _print_breaking_changes(ws_root: Path, started_at: datetime) -> None:
    """One line naming the contracts this update broke, if any.

    The report is written by the cross-repo hooks during this run, so it is only
    ours when it was stamped after we started; an older one belongs to a
    previous update and saying nothing beats attributing it here.
    """
    from rich.markup import escape

    from repowise.core.workspace.breaking_change import load_breaking_change_report

    report = load_breaking_change_report(ws_root)
    if not _report_is_ours(report, started_at):
        return
    gating = _gating_changes(report)
    repos = sorted(
        {ic.repo for c in gating for ic in c.impacted_consumers if ic.repo != c.provider_repo}
    )
    others = len(report.changes) - len(gating)
    if not gating:
        console.print(
            f"\n[dim]{others} contract change(s) recorded; none breaks another repo.[/dim]"
        )
        return
    console.print(
        f"\n[yellow]![/yellow] {len(gating)} breaking contract change(s) impacting "
        f"{escape(', '.join(repos))}"
        + (f", plus {others} non-breaking" if others else "")
        + ". [dim]repowise workspace check[/dim]"
    )
    # Name a few of them, so the count is actionable without a second command.
    # Capped: this runs at the end of an update, and `workspace check` is the
    # place that lists every one.
    for change in gating[:_BREAKING_DETAIL_LINES]:
        hit = sorted(
            {ic.repo for ic in change.impacted_consumers if ic.repo != change.provider_repo}
        )
        console.print(
            f"  [dim]{escape(change.contract_id)}[/dim]  "
            f"{escape(change.provider_repo)} -> {escape(', '.join(hit))}"
        )
    if len(gating) > _BREAKING_DETAIL_LINES:
        console.print(f"  [dim]and {len(gating) - _BREAKING_DETAIL_LINES} more[/dim]")


def _report_is_ours(report: Any, started_at: datetime) -> bool:
    """A report with changes, stamped by this run's cross-repo hooks."""
    if report is None or not report.ran or not report.changes:
        return False
    try:
        return not datetime.fromisoformat(report.generated_at or "") < started_at
    except (TypeError, ValueError):
        return False


def _gating_changes(report: Any) -> list[Any]:
    """Counted the way `workspace check` gates, so the two never contradict."""
    from repowise.core.workspace.breaking_change import SEVERITY_BREAKING

    return [
        c
        for c in report.changes
        if c.severity == SEVERITY_BREAKING
        and any(ic.repo != c.provider_repo for ic in c.impacted_consumers)
    ]


@dataclass
class _DocsTally:
    """How the docs members' single-repo updates ended."""

    updated: int = 0
    failed: int = 0
    deferred: int = 0  # bailed on another update's single-flight lock
    noop: int = 0  # already current / nothing to regenerate
    #: Members that really regenerated, for the cross-repo pass.
    changed: list[str] = field(default_factory=list)


def _workspace_docs_update(
    *,
    ws_root: Path,
    ws_config: Any,
    repo_filter: str | None,
    docs_aliases: set[str],
    start: float,
    started_at: datetime,
    agents_md: bool | None,
    verbose: bool,
    docs_flag: bool | None,
    index_only: bool,
    since: str | None,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    cascade_budget: int | None,
    concurrency: int,
    no_cost_tracking: bool,
    progress: str,
    include_working_tree: bool = False,
) -> None:
    """Update a workspace where at least one stale repo wants docs.

    Docs repos (``docs_aliases``) run through the full single-repo update
    (:func:`run_update`) so their pages, diagrams, and decisions regenerate
    exactly as they would with ``repowise update`` run inside the repo. The
    remaining stale members (index-only + never-indexed first-timers) take the
    fast parallel core path. Cross-repo hooks are suppressed on both and run
    once at the end over every repo that actually changed, so the cross-repo
    layer is never rebuilt from a half-updated set.
    """
    from contextlib import suppress

    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict
    from repowise.core.workspace.update import (
        analysis_config_changed,
        run_cross_repo_hooks,
        sync_workspace_state_from_disk,
    )

    core_results = _update_core_members(
        ws_root, ws_config, repo_filter, docs_aliases, include_working_tree=include_working_tree
    )
    changed_aliases: list[str] = [r.alias for r in core_results if r.updated]
    docs = _update_docs_members(
        ws_root,
        ws_config,
        docs_aliases,
        dict(
            provider_name=provider_name,
            model=model,
            since=since,
            reasoning=reasoning,
            cascade_budget=cascade_budget,
            dry_run=False,
            workspace=False,
            no_workspace=True,
            repo_alias=None,
            index_only=index_only,
            docs_flag=docs_flag,
            full=False,
            agents_md=agents_md,
            concurrency=concurrency,
            no_cost_tracking=no_cost_tracking,
            verbose=verbose,
            # rich even under --progress json: the parent owns the single
            # stdout JSON stream and already sent the console to stderr.
            progress="rich",
            skip_cross_repo_hooks=True,
            include_working_tree=include_working_tree,
        ),
    )
    changed_aliases.extend(docs.changed)

    # The single-repo path only writes each repo's state.json, so re-sync the
    # workspace config: the next run then sees the docs repos up to date.
    with suppress(Exception):
        sync_workspace_state_from_disk(ws_root, ws_config)

    # First-time members the core path just indexed inherit the workspace's
    # distill rewrite-hook verdict, as on the index-only workspace path.
    for entry in ws_config.repos:
        if repo_filter and entry.alias != repo_filter:
            continue
        with suppress(Exception):
            inherit_workspace_distill_verdict((ws_root / entry.path).resolve())

    # Cross-repo hooks once, over the union of index-only and docs repos, or
    # for an edited contracts: or conformance: block with no repo changed.
    if len(ws_config.repos) >= 2 and (
        changed_aliases or analysis_config_changed(ws_config, ws_root)
    ):
        console.print("Running cross-repo analysis...")
        with suppress(Exception):
            run_async(run_cross_repo_hooks(ws_config, ws_root, changed_aliases))
        console.print("[green]Cross-repo analysis updated.[/green]")

    # Every member, idempotently: docs repos were stamped by their own run.
    _refresh_workspace_editor_project_files(
        ws_root=ws_root,
        ws_config=ws_config,
        repo_filter=repo_filter,
        agents_md=agents_md,
    )
    _print_docs_update_summary(core_results, docs, ws_root, start=start, started_at=started_at)


def _update_core_members(
    ws_root: Path,
    ws_config: Any,
    repo_filter: str | None,
    docs_aliases: set[str],
    *,
    include_working_tree: bool,
) -> list[Any]:
    """Index-only and first-time members, on the fast parallel core path.

    ``only_aliases`` bounds the candidate set; update_workspace still re-checks
    staleness, so an up-to-date member in it simply no-ops.
    """
    from repowise.core.workspace import update_workspace

    core_aliases = {
        entry.alias
        for entry in ws_config.repos
        if (repo_filter is None or entry.alias == repo_filter) and entry.alias not in docs_aliases
    }
    if not core_aliases:
        return []

    def _on_start(alias: str) -> None:
        console.print(f"  Updating [bold]{alias}[/bold]...")

    return run_async(
        update_workspace(
            ws_root,
            ws_config,
            only_aliases=core_aliases,
            run_hooks=False,
            dry_run=False,
            include_working_tree=include_working_tree,
            # _reconcile_up_to_date restamped the members that moved.
            restamp_up_to_date=False,
            on_repo_start=_on_start,
            on_repo_done=_print_repo_result,
        )
    )


def _update_docs_members(
    ws_root: Path, ws_config: Any, docs_aliases: set[str], update_kwargs: dict[str, Any]
) -> _DocsTally:
    """Docs members, one at a time, through the full single-repo update."""
    from .command import UpdateOutcome, run_update

    tally = _DocsTally()
    for entry in ws_config.repos:
        if entry.alias not in docs_aliases:
            continue
        repo_path = (ws_root / entry.path).resolve()
        console.print(f"  Updating [bold]{entry.alias}[/bold] (docs)...")
        try:
            outcome = run_update(path=str(repo_path), **update_kwargs)
        except Exception as exc:
            tally.failed += 1
            console.print(f"    [red]✗ {entry.alias}: {exc}[/red]")
            continue
        # A deferred run regenerated nothing, and run_update already printed
        # its in-flight note. Only a real regeneration feeds the cross-repo pass.
        if outcome == UpdateOutcome.DEFERRED:
            tally.deferred += 1
        elif outcome == UpdateOutcome.REGENERATED:
            tally.updated += 1
            tally.changed.append(entry.alias)
        else:  # NOOP / DRY_RUN
            tally.noop += 1
    return tally


def _docs_update_summary(core_updated: int, deferred: int, docs: _DocsTally) -> str:
    parts: list[str] = []
    if docs.updated:
        parts.append(f"{docs.updated} with docs regenerated")
    if core_updated:
        parts.append(f"{core_updated} re-indexed")
    if deferred:
        parts.append(f"{deferred} deferred to an in-flight update")
    if docs.noop:
        parts.append(f"{docs.noop} already current")
    if docs.failed:
        parts.append(f"[red]{docs.failed} failed[/red]")
    return ", ".join(parts) if parts else "nothing to update"


def _print_docs_update_summary(
    core_results: list[Any],
    docs: _DocsTally,
    ws_root: Path,
    *,
    start: float,
    started_at: datetime,
) -> None:
    """The closing line: complete, or deferred when every stale repo only deferred."""
    core_updated = sum(1 for r in core_results if r.updated)
    core_deferred = sum(1 for r in core_results if r.skipped_reason == "in_flight")
    deferred = docs.deferred + core_deferred
    summary = _docs_update_summary(core_updated, deferred, docs)
    console.print()
    # "Complete" would overclaim while the real regeneration runs elsewhere.
    if deferred and not (docs.updated or core_updated):
        console.print(
            f"[yellow]Workspace update deferred[/yellow]: {summary}. "
            f"{_deferred_tail(core_results)} "
            f"[dim]({time.monotonic() - start:.1f}s)[/dim]"
        )
    else:
        console.print(
            f"[green]Workspace update complete[/green]: {summary} "
            f"[dim]({time.monotonic() - start:.1f}s)[/dim]"
        )
        _print_breaking_changes(ws_root, started_at)


def _deferred_tail(core_results: list[Any]) -> str:
    """Where the deferred work is, named by the oldest lock's age.

    The oldest lock is the run most likely to have stopped progressing; "still
    running" with no age is what leaves a wedged update looking healthy.
    """
    from repowise.core.update_lock import UPDATE_LOCK_SUSPECT_AFTER_SECONDS, format_lock_age

    ages = [r.lock_age_seconds for r in core_results if r.lock_age_seconds is not None]
    oldest = max(ages) if ages else None
    if oldest is not None and oldest > UPDATE_LOCK_SUSPECT_AFTER_SECONDS:
        return (
            f"An update has held the lock {format_lock_age(oldest)}, which is "
            "longer than an update normally takes. Check "
            "[bold].repowise/.update.log[/bold]: if it has not been written to "
            "in that time, that update is stuck and can be stopped."
        )
    if oldest is not None:
        return (
            f"A background update has been running {format_lock_age(oldest)}; "
            "watch [bold].repowise/.update.log[/bold]."
        )
    return "A background update is still running; watch [bold].repowise/.update.log[/bold]."


def _refresh_workspace_editor_project_files(
    *,
    ws_root: Path,
    ws_config: Any,
    repo_filter: str | None,
    agents_md: bool | None,
) -> None:
    """Re-stamp each workspace repo's editor files (CLAUDE.md / AGENTS.md).

    Runs on every workspace update so a repo's ``.claude/CLAUDE.md`` "Last
    indexed" stamp tracks the freshly synced index. Each integration decides
    whether to write from the repo's own config (CLAUDE.md defaults on), so we
    pass ``options=None`` for the common case.

    ``agents_md`` is only a per-run override for AGENTS.md generation; when it
    is ``None`` each integration falls back to config. It must NOT gate the
    whole refresh: doing so froze the CLAUDE.md stamp for every workspace user
    who never passed ``--agents-md`` (the default), making the index look stale
    long after it was current. Mirrors the single-repo update path.

    A repo whose fingerprint has not moved since its last refresh is skipped
    (see :mod:`.member_refresh`), so a no-change update costs O(changed repos).
    An explicit ``agents_md`` refreshes every repo, as it may change the files.
    """
    from repowise.cli.commands._index_outcome import refresh_read_snapshots
    from repowise.cli.editor_integrations.defaults import get_default_project_file_overrides
    from repowise.cli.editor_setup import EditorSetupOptions, refresh_editor_project_files

    from .member_refresh import (
        is_unchanged,
        load_member_stamps,
        member_fingerprint,
        save_member_stamps,
    )

    options: EditorSetupOptions | None = None
    if agents_md is not None:
        options = EditorSetupOptions(
            project_file_overrides=get_default_project_file_overrides(agents_md=agents_md),
        )
    stamps = load_member_stamps(ws_root)
    recorded = dict(stamps)
    for entry in ws_config.repos:
        if repo_filter and entry.alias != repo_filter:
            continue
        repo_path = (ws_root / entry.path).resolve()
        if not (repo_path / ".repowise").is_dir():
            continue
        if options is None and is_unchanged(repo_path, stamps):
            continue
        # A failed step drops the stamp, so the next update retries this repo.
        stamps.pop(str(repo_path), None)
        snapshots_written = refresh_read_snapshots(repo_path)
        try:
            refresh_editor_project_files(console, repo_path, options=options)
        except Exception as exc:
            console.print(
                f"  [yellow]{entry.alias}: editor project-file refresh skipped: {exc}[/yellow]"
            )
            continue
        # Taken after the writes, so it describes the state they left.
        fingerprint = member_fingerprint(repo_path) if snapshots_written else None
        if fingerprint is not None:
            stamps[str(repo_path)] = fingerprint
    if stamps != recorded:
        save_member_stamps(ws_root, stamps)

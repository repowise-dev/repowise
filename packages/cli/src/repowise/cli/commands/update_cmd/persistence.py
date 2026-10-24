"""Persistence + health-rescore helpers for ``repowise update``.

Holds the index-only persistence path, the config-triggered full health
re-score, and the small ORM->dict adapter. DB writes delegate to
:mod:`repowise.core.pipeline.incremental` / ``repowise.core.persistence``;
state-file updates and console reporting stay here.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.cli.commands._index_outcome import refresh_read_snapshots  # noqa: F401 (re-export)
from repowise.cli.helpers import (
    as_commit_id,
    console,
    err_console,
    head_commit_ts,
    load_config,
    load_state,
    run_async,
    save_state,
)
from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY
from repowise.core.pipeline import PhaseTimings, timed

from .incremental import _build_repo_graph

log = structlog.get_logger(__name__)



def backfill_docs_pointer(new_state: dict, state: dict) -> None:
    """Carry ``last_sync_commit`` onto a docs pointer that never got one.

    Falsy, not absent. A store that has never had a docs pass carries
    ``last_docs_commit`` as an explicit null, so a membership test reads it as
    "already set" and the repair never runs -- which is what left such a store
    failing every update with "No previous sync found" (#1507 fixed the same
    confusion on the write side, in ``generate``).
    """
    if not state.get("last_docs_commit") and state.get("last_sync_commit"):
        new_state["last_docs_commit"] = state["last_sync_commit"]


def _repair_module_attribution(repo_path: Path) -> int:
    """Re-derive every health row's ``module`` from the repo layout on disk.

    Runs on every ``repowise update``, before the "already up to date" return,
    and is cheap enough to justify that: one pruned directory walk looking for
    manifest filenames, then a write only for rows whose label actually moved.
    A repo already correct writes nothing, so the steady-state cost is the walk.

    It exists because ``module`` is *persisted*, and a change to how it is
    derived would otherwise only reach stored rows when something rewrote them
    — a full health re-score at best, a re-index at worst. Neither is an
    acceptable price for a directory label, and neither is necessary: the label
    is a pure function of ``(file_path, package_roots)``, and package roots are
    just the directories holding a manifest. No parse, no embedding, no model.

    Failure-isolated: an unreadable tree or a missing index returns 0 rather
    than failing the update. The next update retries and nothing else depends
    on the result.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.ingestion.traverser import FileTraverser
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import backfill_module_attribution

    async def _run() -> int:
        # Scan through the traverser so it honours the same gitignore,
        # submodule and exclude boundaries the index does.
        roots = FileTraverser(Path(repo_path)).package_root_dirs()
        engine = create_engine(get_db_url_for_repo(repo_path))
        try:
            await init_db(engine)
            sf = create_session_factory(engine)
            async with get_session(sf) as session:
                repo = await upsert_repository(
                    session, name=Path(repo_path).name, local_path=str(repo_path)
                )
                return await backfill_module_attribution(session, repo.id, roots)
        finally:
            # Matches the other update-time DB opens. File SQLite uses
            # NullPool so nothing is held open across the call, but leaving a
            # live engine behind on a path that runs every update is the kind
            # of thing that turns into a lock report later.
            await engine.dispose()

    try:
        changed = run_async(_run())
    except Exception as exc:
        log.debug("module_attribution_repair_failed", error=str(exc))
        return 0
    if changed:
        console.print(f"[dim]Module attribution: [bold]{changed}[/bold] file(s) corrected[/dim]")
    return changed


@dataclass(frozen=True)
class RescoreCoverage:
    """Coverage for a health re-score, and whether it replaces what is stored."""

    coverage_map: dict[str, dict] = field(default_factory=dict)
    files: list[Any] = field(default_factory=list)
    source_format: str | None = None
    # True for a reingestion, including an empty one that must clear stored rows.
    authoritative: bool = False
    # How the reingested report mapped (``CoverageProvenance``); None when reused.
    provenance: Any = None


async def _coverage_for_rescore(
    session: Any,
    repo_id: str,
    repo_path: Path,
    parsed_files: list[Any],
) -> RescoreCoverage:
    """Coverage to feed a health re-score, preserved across updates.

    Re-discover and re-resolve the reports when one was written after the
    last ingest (``CoverageConfig.reingest``; ``coverage.reingest_on_update``
    forces it on or off), else reload the rows already persisted.
    """
    from repowise.core.analysis.health.coverage import (
        CoverageConfig,
        build_coverage_map,
    )
    from repowise.core.persistence.crud import load_coverage_map
    from repowise.core.pipeline.incremental.loaders import coverage_reports_to_reread
    from repowise.core.repo_config import load_repo_config

    cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))

    # ``None`` keeps the stored rows (e.g. from `coverage add`).
    reports = await coverage_reports_to_reread(session, repo_id, Path(repo_path), cfg)
    if reports is not None:
        if reports:
            repo_keys = {pf.file_info.path for pf in parsed_files}
            resolved, _errors = build_coverage_map(
                repo_path,
                list(reports),
                repo_keys,
                coverage_format=cfg.format,
                strip_prefix=cfg.strip_prefix,
                path_prefix=cfg.path_prefix,
                report_prefixes=reports,
                ignore=cfg.ignore,
            )
            if resolved.coverage_map:
                return RescoreCoverage(
                    resolved.coverage_map,
                    resolved.files,
                    resolved.source_format,
                    authoritative=True,
                    provenance=resolved.provenance,
                )

        # Reingestion is authoritative. Falling back to old rows when a report
        # disappeared (or now maps to nothing) makes this config refresh differ
        # from a clean index and then incorrectly stamps stale data current.
        return RescoreCoverage(authoritative=True)

    coverage_map = await load_coverage_map(session, repo_id)
    source_format = next((e["source_format"] for e in coverage_map.values()), None)
    return RescoreCoverage(coverage_map, source_format=source_format)


async def _persist_partial_health(
    session: Any, repo_id: str, report: Any, repo_path: Any = None
) -> None:
    """Upsert health findings + metrics for the changed-files subset.

    Delegates to :mod:`repowise.core.pipeline.incremental` — the logic moved
    to core so workspace updates can reuse the incremental path. ``repo_path``
    is what lets it also re-score the git-derived markers on files this run did
    not walk; without it those markers keep whatever the last full index said.
    """
    from repowise.core.pipeline.incremental import persist_partial_health

    await persist_partial_health(session, repo_id, report, repo_path)


async def _persist_incremental_commits(
    session: Any, repo_id: str, repo_path: Any, *, timings: PhaseTimings | None = None
) -> None:
    """Capture + upsert ``git_commits`` rows for commits new since the last index.

    Delegates to :mod:`repowise.core.pipeline.incremental`. ``timings`` carries
    its ``persist.commits.*`` sub-rows into the run's phase table.
    """
    from repowise.core.pipeline.incremental import persist_incremental_commits

    await persist_incremental_commits(session, repo_id, repo_path, timings=timings)


def stamp_head_commit(repo_path: Any, head: str | None) -> None:
    """Advance the persisted ``repositories.head_commit`` to *head*.

    The "no changed files" and "already up to date" fast paths in
    ``update_command`` write ``state.json`` and return without touching the DB.
    But the server's ``/repos`` endpoint and the MCP ``_meta`` freshness check
    read the indexed commit from the ``repositories`` row, not from
    ``state.json`` — so skipping this write pinned the freshness signal at the
    last full index, keeping "index behind checkout" stuck after a successful
    update. Keep the DB stamp in lockstep with ``state.json``. Also self-heals
    a row left stale by a pre-fix run: any later update re-stamps it.
    """
    if not head:
        return

    # One stamper for both update paths: delegate to the core implementation
    # the workspace updater uses. It touches only head_commit/updated_at on an
    # existing row (the old upsert here clobbered url/default_branch with
    # defaults), creates the row when missing from an existing store, and
    # no-ops when no store exists at all instead of conjuring an empty DB. A
    # configured database counts as one, which the repo-local file check this
    # used to make could never see.
    from repowise.core.workspace.update import reconcile_repo_head_commit

    run_async(reconcile_repo_head_commit(Path(repo_path), head))


def heal_commit_offsets(repo_path: Any) -> None:
    """Fill ``git_commits.committed_offset_minutes`` on a quiet repo.

    The commit walk captures this offset, so any update that ingests commits
    backfills it on the way past. A repo whose HEAD hasn't moved never gets
    there — it takes the "already up to date" fast path — which would strand an
    index written before the column existed with a UTC-only punch card until
    someone re-indexed. Nobody should have to re-index for a new column, so the
    fast path calls this too.

    Cheap enough to run unconditionally: one indexed SELECT that returns nothing
    once the column is filled, and no git at all in that case. Best-effort — a
    failure here must never turn a clean no-op into an error.
    """
    from repowise.core.persistence.database import has_db_store

    root = Path(repo_path)
    if not has_db_store(root):
        return

    async def _run() -> None:
        from repowise.core.ingestion.git_indexer import GitIndexer
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
        )
        from repowise.core.persistence.crud import get_repository_by_path
        from repowise.core.persistence.database import resolve_db_url
        from repowise.core.pipeline.incremental import reconcile_commit_offsets

        engine = create_engine(resolve_db_url(root))
        try:
            # Migrates first: on the fast path this may be the run that adds the
            # column the backfill is about to fill.
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(root))
                if repo is None:
                    return
                await reconcile_commit_offsets(session, repo.id, GitIndexer(root))
        finally:
            await engine.dispose()

    with contextlib.suppress(Exception):
        run_async(_run())


# A repair window that has grown past this many commits stopped being a repair
# and became a re-index: the diff it forces every update to walk is no longer
# change-sized. Bounding it is what stops a step that fails on every run from
# pinning the window open forever.
_REPAIR_MAX_COMMITS = 500


def record_repair_marker(new_state: dict, prior_state: dict, failed_steps: list[str]) -> None:
    """Record, or clear, the commit range this update failed to fully persist.

    ``last_sync_commit`` advances whether or not the persist steps succeeded, so
    a failed step used to take its commit range with it: nothing ever revisited
    those commits, and their git metadata, health rows, symbols or edges were
    skipped permanently and announced exactly once in the completion panel. The
    marker keeps the *old* pointer so the next update diffs from there and
    re-covers the range, while the pointer itself stays current for every other
    reader of the state file.

    Carrying the oldest unrepaired commit forward rather than the newest is the
    part that makes it hold: three failed runs in a row still have to re-cover
    all three ranges, not just the last one.

    Only range-scoped failures land here (see ``persist_incremental_index``),
    so a repo-wide step that fails on every single run cannot keep the marker
    alive: it heals itself on the next update instead.
    """
    if not failed_steps:
        new_state.pop("pending_repair", None)
        return
    prior = prior_state.get("pending_repair")
    if not isinstance(prior, dict):  # a hand-edited or truncated state file
        prior = {}
    from_commit = prior.get("from_commit") or prior_state.get("last_sync_commit")
    if not from_commit:
        # Nothing to diff back to (a first index, or a state file with no
        # pointer). Widening from an unknown base is not something we can do
        # safely, so leave no marker rather than a broken one.
        new_state.pop("pending_repair", None)
        return
    # Union, not replace: the marker names one range, so it has to name
    # everything still unrepaired in it. Run 1 failing the git persist and run 2
    # failing only the health persist leaves both unrepaired over the same span.
    prior_steps = prior.get("steps")
    prior_steps = prior_steps if isinstance(prior_steps, list) else []
    new_state["pending_repair"] = {
        "from_commit": from_commit,
        "steps": sorted({*prior_steps, *failed_steps}),
    }


def resolve_repair_base(
    repo_path: Any, state: dict, base_ref: str, head: str | None
) -> tuple[str, str | None]:
    """Widen *base_ref* back to an unrepaired range, or report giving up on it.

    Returns ``(base_ref, give_up_reason)``. The reason is non-None exactly when
    the marker should be dropped without being honoured, and it is written for
    the user rather than the log.

    Three things have to hold before the marker is honoured, and each one closes
    a way this could go wrong:

    * the recorded commit still resolves and is an *ancestor* of the current
      base, so honouring it can only ever move the diff backwards. Without the
      ancestry check a rebase, a branch switch or a docs-mode base that is
      already older would let the marker move the base *forward* and skip the
      very commits it exists to re-cover;
    * the range is still bounded (see ``_REPAIR_MAX_COMMITS``);
    * it differs from the base we already have, so a marker that has effectively
      caught up costs nothing.
    """
    import subprocess

    marker = state.get("pending_repair")
    from_commit = marker.get("from_commit") if isinstance(marker, dict) else None
    if not from_commit or from_commit == base_ref:
        return base_ref, None
    if as_commit_id(from_commit) is None or as_commit_id(base_ref) is None:
        return base_ref, None

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(repo_path), *args], capture_output=True, timeout=30, text=True
        )

    try:
        if _git("merge-base", "--is-ancestor", "--end-of-options", from_commit, base_ref).returncode != 0:
            # Non-zero covers both "not an ancestor" (rebased, force-pushed,
            # branch switched) and "cannot resolve that object" (gc'd, or a
            # shallow clone that never fetched it), and git does not
            # distinguish them by exit code. Say both rather than assert one.
            return base_ref, (
                f"the recorded commit {from_commit[:8]} is not an ancestor of this "
                "branch's history, or is not present in this clone"
            )
        counted = _git("rev-list", "--count", "--end-of-options", f"{from_commit}..{head or 'HEAD'}")
        if counted.returncode == 0 and int(counted.stdout.strip() or 0) > _REPAIR_MAX_COMMITS:
            return base_ref, (
                f"the range has grown past {_REPAIR_MAX_COMMITS} commits, which is "
                "a re-index rather than a repair"
            )
    except Exception:
        # git being unavailable is not a reason to widen blindly.
        return base_ref, None

    return from_commit, None


def _rescore_stale_findings(
    repo_path: Any,
    graph_builder: Any,
    state: dict,
    *,
    head_ts: float | None,
    parsed_files: list | None,
    exclude_patterns: list[str] | None,
    force_full_rescore: bool,
    timings: PhaseTimings | None,
) -> tuple[Any, Any, Any]:
    """Re-score health and rescan security repo-wide when they are stale.

    Returns the ``last_full_rescore_at``, ``health_analyzer_version`` and
    ``security_scanner_version`` stamps the new state should carry.
    """
    # Periodic idle-file health re-score (#728): git_metadata is now fresh in the
    # DB, so re-score every file's findings against the decayed inputs. Gated to
    # ~weekly since only the findings (not their cheap git inputs) lag here.
    # ``force_full_rescore`` is the config-changed caller: a config edit
    # invalidates every persisted score, and the partial health update above
    # only reached the changed files.
    last_full_rescore_at = state.get("last_full_rescore_at")
    health_analyzer_version = state.get("health_analyzer_version")
    rescored = False
    if force_full_rescore or full_rescore_due(state, head_ts):
        with timed(timings, "rescore"):
            rescored = run_decay_health_rescore(
                repo_path, graph_builder, parsed_files or [], exclude_patterns or []
            )
    if rescored:
        # Only the time gate reads this, and it treats a non-numeric value as
        # "never re-scored", so leave a real stamp alone rather than writing
        # None over it when git gave us no timestamp.
        if head_ts is not None:
            last_full_rescore_at = head_ts
        from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

        health_analyzer_version = HEALTH_ANALYZER_VERSION
    if force_full_rescore and not rescored:
        raise RuntimeError(
            "Configuration-triggered health re-score failed; the previous "
            "fingerprint was retained so the next update retries."
        )

    # Same drift check for the security scanner (#3072): a version bump means
    # every unchanged file's stored findings may be stale, since the normal
    # persist above only rescanned the changed files.
    security_scanner_version = state.get("security_scanner_version")
    if security_scanner_changed(state):
        from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION

        with timed(timings, "security_rescan"):
            if run_full_security_rescan(repo_path, exclude_patterns or []):
                security_scanner_version = SECURITY_SCANNER_VERSION
    return last_full_rescore_at, health_analyzer_version, security_scanner_version


def _stamp_git_history(
    new_state: dict, full_git_summary: Any | None, failed_steps: list[str]
) -> None:
    """Record git history coverage and version when this run walked full history."""
    if full_git_summary is None:
        return
    coverage = getattr(full_git_summary, "history_coverage", None)
    if coverage is not None:
        new_state["git_history_coverage"] = coverage.to_dict()
    else:
        new_state.pop("git_history_coverage", None)
    # Every row was rewritten under the current rules, unless they never
    # reached the store; then the next update walks again.
    if "Git persist" not in failed_steps:
        new_state[GIT_HISTORY_VERSION_KEY] = GIT_HISTORY_VERSION


def _stamp_scope(repo_path: Any, new_state: dict, failed_steps: list[str]) -> None:
    """Stamp the index scope, counting this run's failed steps as unavailable."""
    from repowise.core.index_scope import resolve_index_scope, stamp_index_scope

    scope = resolve_index_scope(new_state, load_config(repo_path))
    unavailable = set(scope["analysis"]["unavailable"])
    unavailable.update(str(step) for step in failed_steps)
    stamp_index_scope(
        new_state,
        load_config(repo_path),
        git_history_coverage=new_state.get("git_history_coverage"),
        analysis={
            "unavailable": sorted(unavailable),
            "skipped": scope["analysis"]["skipped"],
        },
    )


def stamp_file_page_scope(
    repo_path: Any,
    state: dict,
    parsed_files: list | None,
    graph_builder: Any,
    git_meta_map: dict | None,
) -> None:
    """Recount ``index_scope.file_pages`` from this run's parse and the stored pages.

    Eligibility applies selection's gates except the cap, as init's figure does;
    an unreadable store keeps the previous figures.
    """
    if not parsed_files or graph_builder is None:
        return
    from repowise.core.generation import GenerationConfig
    from repowise.core.generation.selection.selector import eligible_file_page_paths
    from repowise.core.index_scope import file_page_counts, resolve_index_scope, stamp_index_scope
    from repowise.core.repo_config import load_repo_config

    try:
        pagerank = graph_builder.pagerank()
    except Exception as exc:  # a released graph cannot rank anything
        log.debug("file_page_scope_skipped", error=str(exc))
        return
    stored = run_async(_load_page_paths_and_hotspots(repo_path))
    if stored is None:
        return
    page_paths, hotspots = stored
    for path, meta in (git_meta_map or {}).items():
        if meta.get("is_hotspot"):
            hotspots.add(path)
        else:
            hotspots.discard(path)
    eligible = eligible_file_page_paths(
        parsed_files,
        pagerank,
        hotspots=hotspots,
        dedupe_near_clones=GenerationConfig.from_repo_config(
            load_repo_config(Path(repo_path))
        ).dedupe_near_clones,
    )
    config = load_config(repo_path)
    stamp_index_scope(
        state,
        config,
        file_pages=file_page_counts(
            configured_cap=resolve_index_scope(state, config)["file_pages"]["configured_cap"],
            eligible=len(eligible),
            generated=len(eligible & page_paths),
        ),
    )


async def _load_page_paths_and_hotspots(repo_path: Any) -> tuple[set[str], set[str]] | None:
    """Paths with a live file page, and the stored hotspot paths."""
    from sqlalchemy import select

    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )
    from repowise.core.persistence.models import GitMetadata, Page

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            pages = await session.execute(
                select(Page.target_path).where(
                    Page.repository_id == repo.id,
                    Page.page_type == "file_page",
                    Page.freshness_status != "tombstone",
                )
            )
            hotspots = await session.execute(
                select(GitMetadata.file_path).where(
                    GitMetadata.repository_id == repo.id, GitMetadata.is_hotspot.is_(True)
                )
            )
            return set(pages.scalars()), set(hotspots.scalars())
    except Exception as exc:
        log.warning("file_page_scope_read_failed", error=str(exc))
        return None
    finally:
        await engine.dispose()


def _persist_index_only_update(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    dead_code_report: Any,
    partial_health_report: Any,
    state: dict,
    head: str | None,
    start: float,
    changed_paths: list[str],
    doc_drift_report: Any | None = None,
    file_diffs: list | None = None,
    knowledge_graph_result: Any | None = None,
    parsed_files: list | None = None,
    source_map: dict | None = None,
    degraded: list[str] | None = None,
    pages_rendered: int = 0,
    template_wiki: bool = False,
    git_decay_map: dict | None = None,
    exclude_patterns: list[str] | None = None,
    head_ts: float | None = None,
    force_full_rescore: bool = False,
    full_git_summary: Any | None = None,
    reconcile_full_scope: bool = False,
    full_generation_page_ids: set[str] | None = None,
    stamp_renderer: bool = True,
    dependency_fingerprints: dict[str, str] | None = None,
    vector_store: Any | None = None,
    require_config_rebuild_success: bool = False,
    history_window_changed: bool = False,
    timings: PhaseTimings | None = None,
) -> None:
    """Persist the index-only update (graph + symbols + git + dead-code + health + KG),
    save state, and print the completion line. No LLM regeneration.

    ``force_full_rescore`` runs the full health re-score regardless of the
    periodic gate. Set by the config-changed caller, which relies on this path
    rather than re-scoring separately and returning — doing that advanced
    ``last_sync_commit`` past commits it never indexed.

    ``stamp_renderer`` False keeps the stored renderer fingerprint: the full
    re-render a renderer change asked for failed, so the next run retries it.

    ``timings`` is the run's phase table. Its ``run`` row is closed here, at
    the state write, and the whole table lands in ``state.json`` as
    ``phase_timings`` the way ``init`` records its own run.

    DB persistence delegates to :mod:`repowise.core.pipeline.incremental`;
    state-file updates and console reporting stay here. Best-effort steps
    that fail land in ``degraded`` and surface in the completion panel.
    """
    from repowise.core.pipeline.incremental import persist_incremental_index

    degraded = degraded if degraded is not None else []
    # Failures whose input was this commit range, kept apart from ``degraded``
    # (which also collects repo-wide and non-DB failures) because only these
    # strand data the advancing sync pointer would otherwise skip forever.
    failed_steps: list[str] = []
    degraded_before_persist = len(degraded)
    run_async(
        persist_incremental_index(
            repo_path,
            graph_builder,
            git_meta_map,
            dead_code_report,
            partial_health_report,
            changed_paths,
            doc_drift_report=doc_drift_report,
            file_diffs=file_diffs,
            knowledge_graph_result=knowledge_graph_result,
            parsed_files=parsed_files,
            source_map=source_map,
            git_decay_map=git_decay_map,
            full_git_summary=full_git_summary,
            history_window_changed=history_window_changed,
            reconcile_full_scope=reconcile_full_scope,
            full_generation_page_ids=full_generation_page_ids,
            vector_store=vector_store,
            log=console.print,
            degraded=degraded,
            failed_steps=failed_steps,
            timings=timings,
        )
    )
    if require_config_rebuild_success and len(degraded) > degraded_before_persist:
        raise RuntimeError(
            "Configuration rebuild did not update every dependent store; "
            "the previous fingerprint was retained so the next update retries."
        )
    from repowise.cli.helpers import config_fingerprint
    from repowise.core.repo_config import config_dependency_fingerprints

    from .command import _current_renderer_fingerprint

    last_full_rescore_at, health_analyzer_version, security_scanner_version = (
        _rescore_stale_findings(
            repo_path,
            graph_builder,
            state,
            head_ts=head_ts,
            parsed_files=parsed_files,
            exclude_patterns=exclude_patterns,
            force_full_rescore=force_full_rescore,
            timings=timings,
        )
    )
    new_state = {
        **state,
        "last_sync_commit": head,
        "last_full_rescore_at": last_full_rescore_at,
        "health_analyzer_version": health_analyzer_version,
        "security_scanner_version": security_scanner_version,
        "config_fingerprint": config_fingerprint(repo_path),
        "config_dependency_fingerprints": (
            dependency_fingerprints
            if dependency_fingerprints is not None
            else config_dependency_fingerprints(repo_path)
        ),
        # Record the renderer this run rendered with. Without this an index-only
        # update that regenerated stale file pages leaves the stored fingerprint
        # at its old value, so ``renderer_changed`` stays true and every later
        # update pays a full reparse forever even though nothing is stale. This
        # is the keyless hot path (the post-commit hook), so a lingering reparse
        # here is exactly the cost the mode exists to avoid.
        "renderer_fingerprint": (
            _current_renderer_fingerprint(repo_path)
            if stamp_renderer
            else state.get("renderer_fingerprint")
        ),
    }
    _stamp_git_history(new_state, full_git_summary, failed_steps)
    # Before save_state, and reading ``state`` (the pre-update dict) for the old
    # pointer: this is what keeps a degraded run recoverable now that the
    # pointer below advances to head regardless.
    record_repair_marker(new_state, state, failed_steps)
    if failed_steps:
        console.print(
            "[yellow]Some data for this commit range was not persisted; "
            "the next update will re-cover it.[/yellow]"
        )
    backfill_docs_pointer(new_state, state)
    if knowledge_graph_result is not None:
        try:
            from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json

            save_knowledge_graph_json(repo_path, knowledge_graph_result)
            new_state["knowledge_graph"] = build_kg_state(knowledge_graph_result)
        except Exception as exc:
            console.print(f"[yellow]Knowledge-graph export skipped: {exc}[/yellow]")
            degraded.append(f"Knowledge-graph export: {exc}")
    if timings is not None:
        timings.stop("run")
        new_state["phase_timings"] = timings.totals
    _stamp_scope(repo_path, new_state, failed_steps)
    stamp_file_page_scope(repo_path, new_state, parsed_files, graph_builder, git_meta_map)
    save_state(repo_path, new_state)
    elapsed = time.monotonic() - start
    from repowise.core.index_scope import upgrade_command

    from .reporting import show_index_only_completion

    show_index_only_completion(
        graph_builder=graph_builder,
        dead_code_report=dead_code_report,
        changed_count=len(changed_paths),
        git_files=len(git_meta_map or {}),
        elapsed=elapsed,
        degraded=degraded,
        pages_rendered=pages_rendered,
        template_wiki=template_wiki,
        changed_paths=changed_paths,
        upgrade_command=upgrade_command(new_state.get("index_scope")),
    )


class PageCheckpointer:
    """Best-effort per-page persistence during update-time generation.

    The final persist runs only after ALL pages are generated, so a crash
    midway used to re-bill every already-generated page on the next run:
    nothing durable recorded them. This sink upserts each page's DB row as
    it lands; on the rerun the generator's prompt-hash skip sees the
    persisted content and never re-calls the LLM for it.

    Purely a checkpoint: the final persist re-upserts every page anyway
    (idempotent), so any failure here flips the sink off, records one
    degraded entry, and the run continues exactly as before. Writes are
    serialized through a queue consumed by a single task, one short
    transaction per page, on a dedicated engine — generation itself only
    writes the vector store, so wiki.db is uncontended during this window.
    """

    def __init__(self, repo_path: Any, repo_name: str) -> None:
        self.repo_path = repo_path
        self.repo_name = repo_name
        self.failure: str | None = None
        self.persisted = 0
        self._queue: Any | None = None
        self._task: Any | None = None
        self._engine: Any | None = None
        self._session_factory: Any | None = None

    async def start(self) -> None:
        import asyncio

        try:
            from repowise.cli.helpers import get_db_url_for_repo
            from repowise.core.persistence import create_engine, create_session_factory

            self._engine = create_engine(get_db_url_for_repo(self.repo_path))
            self._session_factory = create_session_factory(self._engine)
            self._queue = asyncio.Queue()
            self._task = asyncio.create_task(self._drain())
        except Exception as exc:
            self.failure = str(exc)

    def on_page_ready(self, page: Any) -> None:
        """Sync callback handed to ``generate_all`` — enqueue, never block."""
        if self._queue is not None and self.failure is None:
            self._queue.put_nowait(page)

    async def _drain(self) -> None:
        from repowise.core.persistence import (
            get_session,
            upsert_page_from_generated,
            upsert_repository,
        )

        repo_id: str | None = None
        while True:
            page = await self._queue.get()
            if page is None:
                return
            if self.failure is not None:
                continue  # keep consuming so close() never hangs
            try:
                async with get_session(self._session_factory) as session:
                    if repo_id is None:
                        repo = await upsert_repository(
                            session, name=self.repo_name, local_path=str(self.repo_path)
                        )
                        repo_id = repo.id
                    await upsert_page_from_generated(session, page, repo_id)
                self.persisted += 1
            except Exception as exc:
                self.failure = str(exc)
                log.warning("page_checkpoint_disabled", error=str(exc))

    async def close(self) -> None:
        if self._queue is not None and self._task is not None:
            self._queue.put_nowait(None)
            await self._task
        if self._engine is not None:
            await self._engine.dispose()


def _persist_full_update(
    *,
    repo_path: Any,
    repo_name: str,
    generated_pages: list,
    file_diffs: list,
    git_meta_map: dict,
    new_decision_markers: list,
    decision_vector_store: Any | None,
    provider: Any,
    partial_health_report: Any,
    dead_code_report: Any,
    graph_builder: Any,
    knowledge_graph_result: Any | None,
    degraded: list[str],
    doc_drift_report: Any | None = None,
    decay_paths: list[str] | None = None,
    parsed_files: list | None = None,
    source_map: dict | None = None,
    git_decay_map: dict | None = None,
    full_git_summary: Any | None = None,
    reconcile_full_scope: bool = False,
    reconcile_full_generation: bool = False,
    require_config_rebuild_success: bool = False,
    require_decision_persist_success: bool = False,
    history_window_changed: bool = False,
    failed_steps: list[str] | None = None,
    timings: PhaseTimings | None = None,
) -> int:
    """Persist a full (LLM-regenerating) update in one transaction.

    Mirrors :func:`repowise.core.pipeline.incremental.persist_incremental_index`:
    one engine, one session, so a crash rolls the whole batch back instead of
    leaving pages committed but health/git/graph rows from the previous
    commit (the old shape here was eight separate auto-committing sessions).
    Page upserts fail loudly — pages are the point of docs mode; every other
    step degrades into ``degraded`` with a logged warning. FTS indexing stays
    outside the transaction: it is rebuildable and non-transactional anyway.

    Returns the repository's total page count after the write, so the caller
    can stamp ``state["total_pages"]`` with the real DB total (regeneration
    upserts existing pages, so accumulating ``len(generated_pages)`` inflates
    the count forever).

    ``failed_steps`` (when supplied) collects the range-scoped failed steps,
    as :func:`~repowise.core.pipeline.incremental.persist_incremental_index` does.
    """
    return run_async(
        _persist_full_update_async(
            repo_path=repo_path,
            repo_name=repo_name,
            generated_pages=generated_pages,
            file_diffs=file_diffs,
            git_meta_map=git_meta_map,
            new_decision_markers=new_decision_markers,
            decision_vector_store=decision_vector_store,
            provider=provider,
            partial_health_report=partial_health_report,
            dead_code_report=dead_code_report,
            graph_builder=graph_builder,
            knowledge_graph_result=knowledge_graph_result,
            degraded=degraded,
            doc_drift_report=doc_drift_report,
            decay_paths=decay_paths,
            parsed_files=parsed_files,
            source_map=source_map,
            git_decay_map=git_decay_map,
            full_git_summary=full_git_summary,
            reconcile_full_scope=reconcile_full_scope,
            reconcile_full_generation=reconcile_full_generation,
            require_config_rebuild_success=require_config_rebuild_success,
            require_decision_persist_success=require_decision_persist_success,
            history_window_changed=history_window_changed,
            failed_steps=failed_steps,
            timings=timings,
        )
    )


async def _persist_full_update_async(
    *,
    repo_path: Any,
    repo_name: str,
    generated_pages: list,
    file_diffs: list,
    git_meta_map: dict,
    new_decision_markers: list,
    decision_vector_store: Any | None,
    provider: Any,
    partial_health_report: Any,
    dead_code_report: Any,
    graph_builder: Any,
    knowledge_graph_result: Any | None,
    degraded: list[str],
    doc_drift_report: Any | None = None,
    decay_paths: list[str] | None = None,
    parsed_files: list | None = None,
    source_map: dict | None = None,
    git_decay_map: dict | None = None,
    full_git_summary: Any | None = None,
    reconcile_full_scope: bool = False,
    reconcile_full_generation: bool = False,
    require_config_rebuild_success: bool = False,
    require_decision_persist_success: bool = False,
    history_window_changed: bool = False,
    failed_steps: list[str] | None = None,
    timings: PhaseTimings | None = None,
) -> int:
    from .full_persist import FullUpdatePersist, write_full_update

    ctx = FullUpdatePersist(
        repo_path=repo_path,
        repo_name=repo_name,
        generated_pages=generated_pages,
        file_diffs=file_diffs,
        git_meta_map=git_meta_map,
        new_decision_markers=new_decision_markers,
        decision_vector_store=decision_vector_store,
        provider=provider,
        partial_health_report=partial_health_report,
        dead_code_report=dead_code_report,
        graph_builder=graph_builder,
        knowledge_graph_result=knowledge_graph_result,
        doc_drift_report=doc_drift_report,
        decay_paths=decay_paths,
        parsed_files=parsed_files,
        source_map=source_map,
        git_decay_map=git_decay_map,
        full_git_summary=full_git_summary,
        reconcile_full_scope=reconcile_full_scope,
        reconcile_full_generation=reconcile_full_generation,
        full_generation_page_ids=(
            {page.page_id for page in generated_pages} if reconcile_full_generation else None
        ),
        require_config_rebuild_success=require_config_rebuild_success,
        require_decision_persist_success=require_decision_persist_success,
        history_window_changed=history_window_changed,
        # A config scope change re-covers every parsed file, not just the diff.
        changed_paths=(
            [parsed.file_info.path for parsed in parsed_files or []]
            if reconcile_full_scope
            else [fd.path for fd in file_diffs]
        ),
        log=console.print,
        degraded=degraded,
        failed_steps=failed_steps,
        timings=timings,
    )
    return await write_full_update(ctx)


async def _rescore_health_from_db(
    repo_path: Any,
    graph_builder: Any,
    parsed_files: list,
    exclude_patterns: list[str],
) -> None:
    """Full-replace health re-score reading git metadata from the DB.

    Shared by the config-changed rebuild path and the periodic decay re-score
    (#728): both need *every* file re-scored against the current git_metadata —
    not just the changed files — but that metadata is already persisted, so the
    analyzer reads it from the DB (keeping accurate churn / ownership /
    co-change data) rather than the caller's changed-only map. Excluded rows are
    dropped from both the DB and the analyzer input; metrics/findings are
    full-replaced so rows for newly-excluded files disappear.

    The caller supplies an already-built *graph_builder* / *parsed_files* so the
    graph is rebuilt at most once per update.
    """
    import pathspec

    exclude_spec = (
        pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns) if exclude_patterns else None
    )

    async def _rescore() -> None:
        from sqlalchemy import delete, select

        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.analysis.communities import file_community_labels
        from repowise.core.analysis.health import HealthAnalyzer
        from repowise.core.analysis.health.config import HealthConfig
        from repowise.core.analysis.health.coverage import measurement_commit
        from repowise.core.analysis.health.history_refresh import (
            BLAME_MARKERS,
            git_meta_rows_to_map,
        )
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )
        from repowise.core.persistence.crud import (
            save_coverage_files,
            upsert_git_function_blame_bulk,
        )
        from repowise.core.persistence.models import GitMetadata, HealthFinding
        from repowise.core.pipeline.persist import (
            persist_graph_nodes,
            refresh_governance_findings,
            save_full_health_report,
        )
        from repowise.core.pipeline.resume.rehydrate import (
            attach_commit_set_blame,
            attach_stored_commit_shas,
        )
        from repowise.core.workspace.update import get_head_commit

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            repo_id = repo.id

            gm_result = await session.execute(
                select(GitMetadata).where(GitMetadata.repository_id == repo_id)
            )
            git_rows = list(gm_result.scalars().all())
            excluded_git_paths = [
                gm.file_path
                for gm in git_rows
                if exclude_spec is not None and exclude_spec.match_file(gm.file_path)
            ]
            if excluded_git_paths:
                await session.execute(
                    delete(GitMetadata).where(
                        GitMetadata.repository_id == repo_id,
                        GitMetadata.file_path.in_(excluded_git_paths),
                    )
                )
                await session.flush()

            # Every stored column, so a gate added with a new column reaches the
            # detectors here as it does on a full index.
            git_meta_map = git_meta_rows_to_map(
                gm
                for gm in git_rows
                if exclude_spec is None or not exclude_spec.match_file(gm.file_path)
            )
            # No blame index here, so Split File reads the stored commit sets,
            # and blames once the candidates stored before the sets existed.
            await attach_stored_commit_shas(session, repo_id, git_meta_map)
            attach_commit_set_blame(
                repo_path,
                git_meta_map,
                parsed_files,
                git_tier=load_state(Path(repo_path)).get("git_tier"),
            )
            stored_blame_findings: dict[str, list[HealthFinding]] = {}
            for finding in (
                await session.execute(
                    select(HealthFinding).where(
                        HealthFinding.repository_id == repo_id,
                        HealthFinding.biomarker_type.in_(BLAME_MARKERS),
                    )
                )
            ).scalars():
                stored_blame_findings.setdefault(finding.file_path, []).append(finding)

            # Preserve coverage across a re-score. The previous behaviour
            # rebuilt the analyzer with no coverage_map, nulling every file's
            # line/branch coverage even though the coverage_files rows still
            # existed. Reload them (and optionally re-discover a fresh report)
            # so coverage survives `repowise update`.
            coverage = await _coverage_for_rescore(session, repo_id, repo_path, parsed_files)

            analyzer = HealthAnalyzer(
                graph_builder.graph(),
                git_meta_map=git_meta_map,
                parsed_files=parsed_files,
                community_label_map=file_community_labels(graph_builder),
                coverage_map=coverage.coverage_map,
                duplication_cache_dir=Path(repo_path) / ".repowise",
                repo_root=repo_path,
                stored_blame_findings=stored_blame_findings,
            )
            hcfg = HealthConfig.load(repo_path)
            analyzer_config = (
                hcfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])
                if (hcfg.disabled_biomarkers or hcfg.rules)
                else None
            )
            report = analyzer.analyze(analyzer_config)

            console.print(
                f"Health re-score: [cyan]{len(parsed_files)} files[/cyan], "
                f"[yellow]{len(report.findings)} findings[/yellow]"
            )

            # Graph nodes first: the plan annotations the writer below stores
            # read each symbol's ``public_api`` from them.
            await persist_graph_nodes(session, repo_id, graph_builder)

            # Same writer the index's analysis phase uses, so a re-score leaves
            # the derived queues agreeing with the findings it just replaced
            # rather than describing the set it deleted. Read HEAD off disk:
            # this pass just scored the working tree, and the stored column is
            # written by a different step whose ordering is not guaranteed.
            await save_full_health_report(
                session,
                repo_id,
                report,
                analyzed_commit=get_head_commit(Path(repo_path)),
                repo_path=repo_path,
            )
            # That writer replaced every open finding, the governance ones too,
            # and only the governance pass produces those.
            try:
                await refresh_governance_findings(session, repo_id)
            except Exception as exc:
                console.print(f"[yellow]Governance findings skipped: {exc}[/yellow]")
            # Rows for the files blamed above, so the next re-score reads them.
            if report.function_blame_rows:
                await upsert_git_function_blame_bulk(session, repo_id, report.function_blame_rows)
            if coverage.authoritative:
                # Stamp the live HEAD from disk, not the stored
                # ``repo.head_commit`` column. The column names the last
                # *indexed* commit; the coverage just scored describes the
                # working tree, and the two diverge when the tree moved after
                # the last index (issue #1747). HEAD is the right answer for a
                # report written since the last commit; one left over from
                # earlier measures the tree as of its own date, so the stamp
                # moves back to the commit that was HEAD then (issue #3901).
                live_head = get_head_commit(Path(repo_path)) or getattr(repo, "head_commit", None)
                await save_coverage_files(
                    session,
                    repo_id,
                    coverage.files,
                    source_format=coverage.source_format or "lcov",
                    ingested_commit_sha=measurement_commit(
                        repo_path,
                        getattr(coverage.provenance, "measured_at", None),
                        live_head,
                    ),
                    provenance=coverage.provenance,
                )

    await _rescore()


def _run_full_health_rescore(
    repo_path: Any,
    exclude_patterns: list[str],
    state: dict,
    head: str | None,
    curr_fingerprint: str,
    *,
    dependency_fingerprints: dict[str, str] | None = None,
    timings: PhaseTimings | None = None,
) -> None:
    """Rebuild graph and re-run full health analysis when config changed.

    Rebuilds the graph (config edits can change parsing/excludes), then delegates
    to :func:`_rescore_health_from_db` for the full-replace re-score. On success
    advances ``last_sync_commit`` + ``config_fingerprint`` so the config change
    is not re-detected; on failure leaves the fingerprint so the next update
    retries.
    """
    import time

    start = time.monotonic()

    # Share the rebuild path with the incremental update so both produce the
    # same graph (same parser, same framework-aware synthetic edges).
    with timed(timings, "rebuild"):
        # Sources too: the framework-edge pass reads them, and a file it cannot
        # read makes test selection keep every test under its conftests.
        parsed_files, _source_map, graph_builder, _repo_structure, _file_count = _build_repo_graph(
            repo_path,
            exclude_patterns,
            collect_sources=True,
            include_submodules=bool(state.get("include_submodules", False)),
            include_nested_repos=bool(state.get("include_nested_repos", False)),
        )

    # Fan-out metric precompute (mirrors _rebuild_graph_and_git) — the
    # rescore persists graph nodes too, which reads every metric. Best-effort:
    # metrics fall back to lazy computation.
    with contextlib.suppress(Exception):
        run_async(graph_builder.compute_metrics_parallel())

    try:
        with timed(timings, "rescore"):
            run_async(
                _rescore_health_from_db(repo_path, graph_builder, parsed_files, exclude_patterns)
            )
    except Exception as exc:
        # Return without advancing the fingerprint so the next update retries.
        console.print(f"[yellow]Health re-score failed: {exc}[/yellow]")
        raise

    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

    # Same full-replace re-score the periodic gate runs, so it restarts the same
    # cadence. Left unstamped when git is unreadable: the gate cannot fire
    # without a head_ts either.
    new_state = {
        **state,
        "last_sync_commit": head,
        "config_fingerprint": curr_fingerprint,
        # These rows were just rewritten by this analyzer.
        "health_analyzer_version": HEALTH_ANALYZER_VERSION,
    }
    if dependency_fingerprints is not None:
        new_state["config_dependency_fingerprints"] = dependency_fingerprints
    rescored_at = head_commit_ts(repo_path)
    if rescored_at is not None:
        new_state["last_full_rescore_at"] = rescored_at
    if timings is not None:
        timings.stop("run")
        new_state["phase_timings"] = timings.totals
    from repowise.core.index_scope import stamp_index_scope

    stamp_index_scope(
        new_state, load_config(repo_path), analysis={"unavailable": [], "skipped": []}
    )
    save_state(repo_path, new_state)
    elapsed = time.monotonic() - start
    console.print(f"[green]Config-triggered health re-score complete[/green] in {elapsed:.1f}s")


# Periodic idle-file health re-score cadence (#728). The cheap git-metadata
# decay refresh runs every update, but the health *findings* for idle files only
# recover when the analyzer re-scores them. Those biomarkers have a ~125-180d
# half-life, so weekly is ample. The interval is anchored to the repo's
# HEAD commit timestamp (not wall clock), like the git history windows, so it
# stays deterministic on historical checkouts; override for tests.
_FULL_RESCORE_INTERVAL_DAYS = 7.0

# The malformed-value warning is global, not per-repo. `_full_rescore_interval_days`
# is consulted once per repo in a workspace fan-out, `repowise watch` and post-commit
# hook updates, so a naive warn-in-except would spam the terminal once per repo. Hoist
# the once-per-invocation guarantee into a module-level guard (#1371).
_FULL_RESCORE_WARNING_SENT = False


def _full_rescore_interval_days() -> float:
    global _FULL_RESCORE_WARNING_SENT
    raw = os.environ.get("REPOWISE_FULL_RESCORE_INTERVAL_DAYS", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            if not _FULL_RESCORE_WARNING_SENT:
                _FULL_RESCORE_WARNING_SENT = True
                err_console.print(
                    "[yellow]Warning:[/yellow] "
                    f"REPOWISE_FULL_RESCORE_INTERVAL_DAYS must be a number "
                    f"(got '{raw}'); using {_FULL_RESCORE_INTERVAL_DAYS:.0f}-day default"
                )
    return _FULL_RESCORE_INTERVAL_DAYS


def parser_changed(repo_path: Path) -> bool:
    """Whether the stored graph edges were written by a different parser.

    Compares the fingerprint ``persist_incremental_edges`` stamped on the repo
    row with the running ``parser_fingerprint()``. An unstamped row **is** a
    change: ``init`` stamps a fresh store, so an unstamped one was written
    before the stamp or re-indexed over older edges, and the re-parse this
    triggers is the widen that reconciles and stamps it. Reading absence as
    current let a parser-only release report "already up to date" on a quiet
    checkout. A store that cannot be read, has no repository row, or has no
    edges to reconcile, is not.

    Also true when a test file was never checked for reaching code outside its
    imports (an index built before that check): selection would run everything until
    the re-parse stamps it, so a quiet repo must not take the shortcut.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.analysis.test_reachability import unscanned_test_files
    from repowise.core.ingestion.parse_cache import parser_fingerprint
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )
    from repowise.core.pipeline.persist import has_graph_edges

    async def _stored() -> bool:
        engine = create_engine(get_db_url_for_repo(repo_path))
        try:
            sf = create_session_factory(engine)
            async with get_session(sf) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return False
                stored = repo.graph_edges_parser_fingerprint
                if stored is None:
                    # Unstamped with no edges is a store nothing ever indexed
                    # into: there is nothing a re-parse could reconcile.
                    if await has_graph_edges(session, repo.id):
                        return True
                elif stored != parser_fingerprint():
                    return True
                return bool(await unscanned_test_files(session, repo.id))
        finally:
            await engine.dispose()

    try:
        return run_async(_stored())
    except Exception as exc:
        log.debug("parser_change_check_failed", error=str(exc))
        return False


def health_analyzer_changed(state: dict) -> bool:
    """Whether the stored health rows were written by a different analyzer.

    A legacy state file with no stamp is **not** a change: the rows it wrote
    are no more suspect than any other pre-upgrade rows, and treating absence
    as drift would re-score every existing install once on upgrade for no
    stated defect. They pick the stamp up on their first re-score from any
    other trigger.
    """
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

    stored = state.get("health_analyzer_version")
    return stored is not None and stored != HEALTH_ANALYZER_VERSION


def security_scanner_changed(state: dict) -> bool:
    """Whether the stored security findings were written by a different scanner.

    Unlike :func:`health_analyzer_changed`, a state file with no stamp **is** a
    change: the stamp arrived after 0.55.0 (#3086), so an unstamped store was
    written by an older scanner. Reading absence as current would also keep it
    unstamped forever, since only the rescan this gates writes the stamp.
    ``init`` stamps every fresh index, so a new install never reads as stale.
    """
    from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION

    return state.get("security_scanner_version") != SECURITY_SCANNER_VERSION


def rescan_security_if_changed(repo_path: Any, state: dict, exclude_patterns: list[str]) -> bool:
    """Full security rescan when the scanner changed, stamping ``state`` on success.

    Returns True only when it rescanned and stamped, so the caller knows
    ``state`` needs saving. A failed rescan leaves the stamp for a retry.
    """
    if not security_scanner_changed(state):
        return False
    if not run_full_security_rescan(repo_path, exclude_patterns):
        return False
    from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION

    state["security_scanner_version"] = SECURITY_SCANNER_VERSION
    return True


def full_rescore_due(state: dict, head_ts: float | None) -> bool:
    """Whether a full health re-score is due this update.

    Two independent triggers:

    * the analyzer changed since these rows were written — re-score now, so a
      correction lands on the next update rather than waiting out the timer;
    * the periodic idle-file cadence (#728).

    The version check is deliberately ahead of the ``head_ts`` guard: an
    analyzer change invalidates the rows whether or not git is readable.

    Absent ``head_ts`` (git unavailable) → the time gate cannot fire. Absent
    time stamp (never re-scored, or legacy state) → due, to establish the
    recovered baseline.
    """
    if health_analyzer_changed(state):
        return True
    if head_ts is None:
        return False
    last = state.get("last_full_rescore_at")
    if not isinstance(last, (int, float)):
        return True
    return (head_ts - float(last)) >= _full_rescore_interval_days() * 86400.0


def run_decay_health_rescore(
    repo_path: Any,
    graph_builder: Any,
    parsed_files: list,
    exclude_patterns: list[str],
) -> bool:
    """Periodic full health re-score off the already-built graph (#728).

    Reuses the update's ``graph_builder`` / ``parsed_files`` (no second rebuild)
    and reads the just-refreshed git_metadata from the DB, so idle files' health
    findings recover in lockstep with their decayed inputs. Best-effort: returns
    True on success so the caller can stamp ``last_full_rescore_at``; a failure
    is logged and leaves the stamp so the next update retries.
    """
    try:
        run_async(_rescore_health_from_db(repo_path, graph_builder, parsed_files, exclude_patterns))
        return True
    except Exception as exc:
        console.print(f"[yellow]Idle-file health re-score skipped: {exc}[/yellow]")
        return False


def run_full_security_rescan(repo_path: Any, exclude_patterns: list[str]) -> bool:
    """Rescan every tracked working-tree file against the current scanner (#3072).

    Unlike the health decay rescore, there is no stored structural fact to
    replay from the DB: a security finding comes from the raw source text, so
    this does its own full parse (``collect_sources=True``) rather than reusing
    the update's changed-files-only ``parsed_files``. Best-effort: returns True
    on success so the caller can stamp ``security_scanner_version``; a failure
    is logged and leaves the stamp so the next update retries.
    """
    try:
        run_async(_security_rescan_from_full_parse(repo_path, exclude_patterns))
        return True
    except Exception as exc:
        console.print(f"[yellow]Full security rescan skipped: {exc}[/yellow]")
        return False


async def _security_rescan_from_full_parse(repo_path: Any, exclude_patterns: list[str]) -> None:
    """Full-replace security rescan over every tracked working-tree file.

    Symbol-name findings (``SYMBOL_NAME_KINDS``) need the parsed symbols, so
    this does its own full repo parse with ``collect_sources=True`` rather
    than the changed-files-only ``parsed_files`` the rest of ``update`` works
    from, the same way ``init`` has the full source available for its first
    scan. ``replace_findings`` only ever deletes/replaces working-tree rows
    (``commit_sha`` empty); history rows from ``scan --history`` are untouched.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.analysis.security_scan import SecurityScanner, scan_source_map
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    parsed_files, source_map, _graph_builder, _repo_structure, _file_count = _build_repo_graph(
        repo_path, exclude_patterns, collect_sources=True
    )

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            findings_by_file, scanned_paths = scan_source_map(parsed_files, source_map)
            await SecurityScanner(session, repo.id).replace_findings(findings_by_file, scanned_paths)
    finally:
        await engine.dispose()

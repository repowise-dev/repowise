"""Cross-repo hooks run after a workspace update, one timed phase at a time."""

from __future__ import annotations

import asyncio
import json as _json
import logging
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...pipeline.phase_timing import PhaseTimingRecorder
from ..config import WorkspaceConfig

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import ContractStore
    from repowise.core.workspace.cross_repo import CrossRepoOverlay
    from repowise.core.workspace.extractors.service_boundary import ServiceBoundary
    from repowise.core.workspace.repo_index import WorkspaceIndex
    from repowise.core.workspace.system_graph import SystemGraph

_log = logging.getLogger("repowise.workspace.update")

#: Which ``contracts:`` and ``conformance:`` settings the last cross-repo
#: analysis ran with, in ``.repowise-workspace/``.
ANALYSIS_CONFIG_FILENAME = "analysis_config.json"


def _log_phase(timings: PhaseTimingRecorder, phase: str, **counts: int) -> None:
    """Emit one structured line for a finished cross-repo phase.

    Duration *and* the counts the phase produced. A timer alone cannot tell a
    genuine speed-up from a phase that got faster by doing less work, which is
    the specific regression workspace mode has already had once — so the counts
    are not optional decoration.
    """
    detail = ", ".join(f"{k}={v}" for k, v in counts.items())
    _log.info(
        "workspace phase %s: %.2fs%s",
        phase,
        timings.timings.get(phase, 0.0),
        f" ({detail})" if detail else "",
    )


async def run_cross_repo_hooks(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_repos: list[str],
) -> None:
    """Run cross-repo analysis after workspace repos are updated.

    Detects cross-repo co-changes (files committed by the same author within
    a time window across repos) and package/manifest dependencies. Results are
    persisted to ``.repowise-workspace/cross_repo_edges.json`` and loaded by
    the MCP server's :class:`CrossRepoEnricher` at startup. Ends by refreshing
    the workspace store (:mod:`.store`).
    """
    if len(ws_config.repos) < 2:
        return

    from ..repo_index import WorkspaceIndex, open_workspace_index

    timings = PhaseTimingRecorder()

    # One read connection per repo serves all five phases; the HTTP and code
    # extractors read it, so nothing is opened when both are off. Deferred:
    # extraction opens only the repos it re-extracts and the packages they
    # import, so a run with one repo changed loads one repo's symbols.
    contracts = ws_config.contracts
    workspace_index = (
        await open_workspace_index(ws_config, workspace_root, deferred=True)
        if contracts.detect_http or contracts.detect_code_api
        else WorkspaceIndex({})
    )
    try:
        settled, consumers_moved = await _run_phases(
            ws_config, workspace_root, changed_repos, timings, workspace_index
        )
    finally:
        await workspace_index.close()

    # Last: derived components read the system graph the phases just wrote.
    from ..store import refresh_workspace_store

    timings.on_phase_start("workspace_store", None)
    try:
        await refresh_workspace_store(ws_config, workspace_root)
    except Exception:
        _log.warning("Workspace store refresh failed", exc_info=True)
    timings.on_phase_done("workspace_store")
    _log_phase(timings, "workspace_store")

    # A failed extraction or graph is retried on the next update rather than
    # stamped as having run under these settings.
    if settled:
        stamp_analysis_config(ws_config, workspace_root)

    # Each updated repo snapshotted its components before contracts were
    # extracted at its new commit; measure them now. An unchanged provider
    # whose cross-repo consumers moved is measured too; no other member's
    # store is opened.
    for alias in dict.fromkeys([*changed_repos, *sorted(consumers_moved)]):
        entry = ws_config.get_repo(alias)
        if entry is not None:
            await _refresh_contract_facts((workspace_root / entry.path).resolve())


def analysis_config_changed(ws_config: WorkspaceConfig, workspace_root: Path) -> bool:
    """Whether the cross-repo settings differ from those the last analysis ran with.

    No stamp counts as changed: the analysis never ran, or ran before stamps
    existed, and one run settles it. A workspace too small for the analysis
    never changes.
    """
    if len(ws_config.repos) < 2:
        return False
    try:
        stamp = _json.loads(_stamp_path(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not isinstance(stamp, dict) or stamp.get("fingerprint") != ws_config.analysis_fingerprint()


def stamp_analysis_config(ws_config: WorkspaceConfig, workspace_root: Path) -> None:
    """Record the cross-repo settings this analysis ran with. Best-effort."""
    from ...fsutils import atomic_write_text
    from ..config import WORKSPACE_DATA_DIR

    try:
        (workspace_root / WORKSPACE_DATA_DIR).mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            _stamp_path(workspace_root),
            _json.dumps({"fingerprint": ws_config.analysis_fingerprint()}),
        )
    except OSError:
        _log.warning("Could not stamp the cross-repo analysis config", exc_info=True)


def _stamp_path(workspace_root: Path) -> Path:
    from ..config import WORKSPACE_DATA_DIR

    return workspace_root / WORKSPACE_DATA_DIR / ANALYSIS_CONFIG_FILENAME


async def _refresh_contract_facts(repo_path: Path) -> None:
    """Best-effort: a failure leaves the run's contracts unavailable."""
    from ...events.store import refresh_contract_facts
    from ...persistence import create_engine, create_session_factory, get_session
    from ...persistence.crud import get_repository_by_path
    from ...persistence.database import has_db_store, resolve_db_url

    if not has_db_store(repo_path):
        return
    engine = create_engine(resolve_db_url(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is not None:
                await refresh_contract_facts(session, repo.id)
    except Exception:
        _log.warning("Contract facts not refreshed for %s", repo_path, exc_info=True)
    finally:
        await engine.dispose()


async def _run_phases(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_repos: list[str],
    timings: PhaseTimingRecorder,
    workspace_index: WorkspaceIndex,
) -> tuple[bool, set[str]]:
    """The five cross-repo phases, over an already-open workspace index.

    Returns whether extraction and the system graph both succeeded, and the
    repos whose provided contracts or their cross-repo consumer links differ
    from the previous extraction.
    """
    from ..contracts import ContractStore, load_contract_store

    boundaries_by_repo = await _boundaries_phase(ws_config, workspace_root, timings)

    # Snapshot the contracts as they stand on disk BEFORE extraction overwrites
    # them. Two jobs, one read: the baseline for the breaking-change diff, and
    # the source of any rows extraction carries forward instead of recomputing.
    # Loaded here rather than inside extraction so the ordering against the
    # write is explicit and the 360 KB parse happens once.
    previous_store = load_contract_store(workspace_root) or ContractStore()

    overlay, store, extraction_ok = await _analysis_and_extraction_phases(
        ws_config,
        workspace_root,
        changed_repos,
        timings,
        workspace_index,
        boundaries_by_repo,
        previous_store,
    )
    system_graph = await _system_graph_phase(
        ws_config, workspace_root, timings, store, overlay, boundaries_by_repo
    )
    _breaking_change_phase(workspace_root, timings, previous_store, store, extraction_ok)

    # Conformance + cycles — check declared dependency rules and detect circular
    # service dependencies over the freshly-built system graph.
    if system_graph is not None:
        _conformance_phase(ws_config, workspace_root, timings, system_graph)

    _log.info("Cross-repo phase timings (seconds): %s", timings.timings)
    moved = _contract_counts_moved(previous_store, store) if extraction_ok else set()
    return extraction_ok and system_graph is not None, moved


def _contract_counts_moved(previous: ContractStore, store: ContractStore) -> set[str]:
    """Repos whose provided contracts or consumer-link counts differ between the stores.

    Exactly what each repo's component snapshot measures
    (:func:`~repowise.core.events.snapshot.contract_consumer_counts`), so a
    member outside this set has nothing to re-measure.
    """
    from ...events.snapshot import contract_consumer_counts

    before, after = contract_consumer_counts(previous), contract_consumer_counts(store)
    return {alias for alias in {*before, *after} if before.get(alias) != after.get(alias)}


async def _boundaries_phase(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    timings: PhaseTimingRecorder,
) -> dict[str, list[ServiceBoundary]]:
    """Service boundaries, detected once for contract extraction and the system graph."""
    from ..system_graph import _detect_boundaries_by_repo

    boundaries_by_repo: dict[str, list[ServiceBoundary]] = {}
    timings.on_phase_start("boundaries", None)
    try:
        boundaries_by_repo = await asyncio.to_thread(
            _detect_boundaries_by_repo, ws_config, workspace_root
        )
    except Exception:
        _log.warning("Service boundary detection failed", exc_info=True)
    timings.on_phase_done("boundaries")
    _log_phase(
        timings,
        "boundaries",
        repos=len(boundaries_by_repo),
        boundaries=sum(len(b) for b in boundaries_by_repo.values()),
    )
    return boundaries_by_repo


async def _analysis_and_extraction_phases(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_repos: list[str],
    timings: PhaseTimingRecorder,
    workspace_index: WorkspaceIndex,
    boundaries_by_repo: dict[str, list[ServiceBoundary]],
    previous_store: ContractStore,
) -> tuple[CrossRepoOverlay, ContractStore, bool]:
    """Run cross-repo analysis and contract extraction together.

    Returns ``(overlay, store, extraction_ok)``; a failed phase yields an
    empty result.
    """
    from ..contracts import ContractStore, run_contract_extraction
    from ..cross_repo import CrossRepoOverlay, run_cross_repo_analysis

    # Phases 1 and 2 are independent: they read disjoint inputs (git history and
    # manifests vs source files and the symbol index), write different artifacts
    # (cross_repo_edges.json vs contracts.json), and share no mutable state —
    # boundaries_by_repo is computed above and passed in read-only. Both push
    # their blocking work through asyncio.to_thread, so gather genuinely
    # overlaps them rather than interleaving two synchronous bodies.
    # Each coroutine times itself, so running them together does not cost the
    # ability to say which one dominates — the question this instrumentation
    # exists to answer, and one a single duration for the pair would destroy.
    async def _timed(phase: str, coro: Awaitable[Any]) -> Any:
        timings.on_phase_start(phase, None)
        try:
            return await coro
        finally:
            timings.on_phase_done(phase)

    overlay_result, store_result = await asyncio.gather(
        _timed(
            "cross_repo_analysis", run_cross_repo_analysis(ws_config, workspace_root, changed_repos)
        ),
        _timed(
            "contract_extraction",
            run_contract_extraction(
                ws_config,
                workspace_root,
                changed_repos,
                boundaries_by_repo or None,
                previous_store,
                workspace_index,
            ),
        ),
        return_exceptions=True,
    )

    # Cancellation and interpreter-level exits are not phase failures — the old
    # `except Exception` let them propagate by construction, and gather's
    # return_exceptions=True does not. Re-raise them before anything downstream
    # treats a cancelled run as a completed one.
    for result in (overlay_result, store_result):
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result

    overlay = CrossRepoOverlay()
    if isinstance(overlay_result, Exception):
        _log.warning("Cross-repo analysis failed", exc_info=overlay_result)
    else:
        overlay = overlay_result

    store = ContractStore()
    extraction_ok = not isinstance(store_result, Exception)
    if isinstance(store_result, Exception):
        _log.warning("Contract extraction failed", exc_info=store_result)
    else:
        store = store_result

    _log_phase(
        timings,
        "cross_repo_analysis",
        co_changes=len(overlay.co_changes),
        package_deps=len(overlay.package_deps),
    )
    _log_phase(
        timings,
        "contract_extraction",
        contracts=len(store.contracts),
        links=len(store.contract_links),
        # Repos actually walked this run — the number that separates a real
        # speed-up from a phase that got faster by doing less than it claims.
        # Derived from the stamp rather than summing extraction_stats, which
        # also carries the counters of the repos this run skipped.
        repos_extracted=sum(
            1 for p in store.repo_provenance.values() if p.get("extracted_at") == store.generated_at
        ),
        repos_reused=sum(
            1 for p in store.repo_provenance.values() if p.get("extracted_at") != store.generated_at
        ),
    )
    return overlay, store, extraction_ok


async def _system_graph_phase(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    timings: PhaseTimingRecorder,
    store: ContractStore,
    overlay: CrossRepoOverlay,
    boundaries_by_repo: dict[str, list[ServiceBoundary]],
) -> SystemGraph | None:
    """Build the system graph, the service-granular structure every workspace view reads.

    Built after extraction so it folds in the contracts and overlay.
    """
    from ..system_graph import run_system_graph_build

    system_graph: SystemGraph | None = None
    timings.on_phase_start("system_graph", None)
    try:
        system_graph = await run_system_graph_build(
            ws_config, workspace_root, store, overlay, boundaries_by_repo or None
        )
    except Exception:
        _log.warning("System graph build failed", exc_info=True)
    timings.on_phase_done("system_graph")
    _log_phase(
        timings,
        "system_graph",
        nodes=len(system_graph.nodes) if system_graph else 0,
        edges=len(system_graph.edges) if system_graph else 0,
    )
    return system_graph


def _breaking_change_phase(
    workspace_root: Path,
    timings: PhaseTimingRecorder,
    previous_store: ContractStore,
    store: ContractStore,
    extraction_ok: bool,
) -> None:
    """Diff the previous (on-disk) contracts against the fresh set and persist the report.

    Gated on the SHAPE of the result, not on whether an exception escaped.
    Diffing a populated baseline against an empty set reports every contract
    in the workspace as removed, and extraction has more than one way to hand
    back nothing: it raises, or it returns an empty store early because fewer
    than two repos are currently indexed (a repo unmounted, renamed, or having
    just lost its .repowise/). Both would publish themselves as the largest
    breaking change the workspace has ever seen. An empty result is only
    trustworthy when the baseline is empty too.
    """
    from ..breaking_change import run_breaking_change_detection

    if extraction_ok and (store.contracts or not previous_store.contracts):
        report = None
        timings.on_phase_start("breaking_change", None)
        try:
            report = run_breaking_change_detection(workspace_root, previous_store, store)
        except Exception:
            _log.warning("Breaking-change detection failed", exc_info=True)
        timings.on_phase_done("breaking_change")
        _log_phase(
            timings,
            "breaking_change",
            breaking=len(report.changes) if report else 0,
        )
    else:
        _log.warning(
            "Skipping breaking-change detection: extraction produced no contracts "
            "(ok=%s) while the previous artifact holds %d — diffing these would "
            "report the whole workspace as removed",
            extraction_ok,
            len(previous_store.contracts),
        )


def _conformance_phase(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    timings: PhaseTimingRecorder,
    system_graph: SystemGraph,
) -> None:
    """Check declared dependency rules and service cycles over the system graph."""
    from ..conformance import run_conformance_check

    conformance = None
    timings.on_phase_start("conformance", None)
    try:
        conformance = run_conformance_check(ws_config, workspace_root, system_graph)
    except Exception:
        _log.warning("Conformance check failed", exc_info=True)
    timings.on_phase_done("conformance")
    _log_phase(
        timings,
        "conformance",
        rules=conformance.rules_evaluated if conformance else 0,
        violations=len(conformance.violations) if conformance else 0,
        # The true total, not the capped list — a workspace with 500 cycles
        # must not report the same number as one with 50.
        cycles=conformance.total_cycles if conformance else 0,
    )

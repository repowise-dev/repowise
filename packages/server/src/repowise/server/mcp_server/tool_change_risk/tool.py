"""MCP tool for live commit and range change-risk scoring."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import shutil
import subprocess
import time
from typing import Any

from repowise.core.analysis.change_risk import (
    normalize_extensions,
)
from repowise.core.analysis.change_risk.features import revspec_head
from repowise.core.registry import mcp_tool_registry as mcp

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._call_tasks import ends_call_tasks, start_call_task
from repowise.server.mcp_server._helpers import (
    _unsupported_repo_all,
    attach_ignored_arguments,
)
from repowise.server.mcp_server._meta import full_meta
from repowise.server.mcp_server._savings import declare_replaced
from repowise.server.mcp_server._savings.counterfactual import change_risk_replaced_tokens

from . import cache as _cache
from .branches import (
    _branch_overlap_block,
    _independent_changes_block,
    _scan_answer,
    _start_overlap_scan,
)
from .changed import _changed_in_scope, _push_change
from .cross_repo import _cross_repo_block
from .health import (
    _attach_health_references,
    _delta_with_index,
    _evidence_block,
    _repository,
    _scored_repo,
)
from .impacted import _impacted_tests_block, _start_selection
from .patch_coverage import _patch_coverage_block
from .payload import (
    _attach_health,
    _diff_shape_sentence,
    _drill_down,
    _include_blocks,
    _path_patterns,
    _shape_payload,
)
from .prior_fixes import _prior_fixes_block


async def _score_change(
    repo_path: Any,
    revspec: str | None,
    extensions: list[str] | None,
    exclude_patterns: list[str] | None,
    include_paths: tuple[str, ...],
    baseline: int,
) -> tuple[Any, dict | None]:
    """Score the live change: ``(result, None)``, or ``(None, error reply)``."""
    try:
        result = await asyncio.to_thread(
            _pkg.score_live_change,
            repo_path,
            revspec,
            extensions=tuple(extensions or ()),
            exclude_patterns=tuple(exclude_patterns or ()),
            include_paths=include_paths,
            baseline=baseline,
        )
    except ValueError as exc:
        return None, {"error": str(exc)}
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").partition("\nUse '--' to separate paths from revisions")[0]
        detail = detail.strip() or str(exc)
        return None, {"error": f"Could not read change {revspec or 'HEAD'!r}: {detail}"}
    except subprocess.TimeoutExpired:
        return None, {"error": f"git timed out reading change {revspec or 'HEAD'!r}."}
    except FileNotFoundError:
        # Scoring spawns git from several readers, so the missing binary is
        # named once here. Any other missing file is a real bug: re-raise.
        if shutil.which("git") is not None:
            raise
        return None, {
            "error": "git was not found on PATH; change risk reads the repository history."
        }
    return result, None


@contextlib.contextmanager
def _timed(timings: dict[str, int] | None, block: str):
    """Record the ``with`` body's wall time as ``timings[block]``, in whole ms."""
    started = time.perf_counter()
    try:
        yield
    finally:
        if timings is not None:
            timings[block] = round((time.perf_counter() - started) * 1000)


async def _score_beside_selection(
    ctx: Any,
    revspec: str | None,
    finding_id: str | None,
    timings: dict[str, int],
    *score_args: Any,
) -> tuple[Any, dict | None]:
    """Score the change while its test selection starts: ``(result, error)``.

    The tests block joins the selection later. A drill-down runs no blocks, so
    it starts none.
    """
    if finding_id is None:
        _start_selection(ctx, revspec)
    with _timed(timings, "score"):
        return await _score_change(ctx.path, revspec, *score_args)


def _debug_timings(timings: dict[str, int]) -> dict[str, Any]:
    """``block_timing_ms`` under the debug meta switch; nothing in a default response."""
    return {"block_timing_ms": timings} if full_meta() else {}


async def _timed_delta(timings: dict[str, int], compare: Any) -> Any:
    """Await the health comparison, timing it apart from the blocks it overlaps."""
    with _timed(timings, "health_delta"):
        return await compare


def _start_delta(
    ctx: Any,
    revspec: str | None,
    extensions: list[str] | None,
    excludes: tuple[str, ...],
    includes: tuple[str, ...],
    timings: dict[str, int],
) -> asyncio.Task:
    """Begin the health comparison; it ends with the calling request."""
    return start_call_task(
        _timed_delta(
            timings,
            _delta_with_index(ctx, revspec, tuple(extensions or ()), excludes, includes),
        )
    )


async def _early_delta(
    ctx: Any,
    revspec: str | None,
    extensions: list[str] | None,
    exclude_patterns: list[str] | None,
    includes: tuple[str, ...],
    timings: dict[str, int],
) -> tuple[tuple[str, ...], asyncio.Task] | None:
    """``(excludes, comparison)`` for a named commit or range, started before the score.

    A named change never reads the working tree, so its excludes are the
    ``.riskignore`` patterns plus the request's, known before scoring. The
    comparison is mostly Python work and the score mostly git's, so they overlap.
    """
    if not revspec:
        return None
    from repowise.core.analysis.change_risk.service import riskignore_patterns

    try:
        ignores = await asyncio.to_thread(riskignore_patterns, str(ctx.path))
    except Exception:  # the score reads them too, and names the failure
        return None
    excludes = (*ignores, *(exclude_patterns or ()))
    return excludes, _start_delta(ctx, revspec, extensions, excludes, includes, timings)


def _delta_for(
    early: tuple[tuple[str, ...], asyncio.Task] | None,
    ctx: Any,
    revspec: str | None,
    extensions: list[str] | None,
    result: Any,
    includes: tuple[str, ...],
    timings: dict[str, int],
) -> asyncio.Task:
    """The early comparison when it compared what the score counted, else a new one."""
    excludes = tuple(result.riskignore_excludes + result.request_excludes)
    if early is not None and early[0] == excludes:
        return early[1]
    if early is not None:
        early[1].cancel()
    return _start_delta(ctx, revspec, extensions, excludes, includes, timings)


async def _attach_change_blocks(
    ctx: Any,
    payload: dict,
    result: Any,
    revspec: str | None,
    extensions: list[str] | None,
    changed_read: tuple[Any, Any],
    collector: OmissionCollector,
    include_paths: tuple[str, ...] = (),
    tests_detail: bool = False,
    timings: dict[str, int] | None = None,
) -> None:
    """Attach the tests, patch coverage, prior fixes and cross-repo blocks, in that order.

    *timings*, when given, collects each block's wall time in ms.
    """
    changed, changed_error = changed_read
    with _timed(timings, "impacted_tests"):
        payload["impacted_tests"] = await _impacted_tests_block(
            ctx,
            changed,
            changed_error,
            collector,
            revspec=revspec,
            working_tree=result.working_tree,
            owned_excludes=result.owned_excludes,
            detail=tests_detail,
        )
    with _timed(timings, "patch_coverage"):
        patch_changed, patch_error, patch_label, patch_revspec = await _push_change(
            str(ctx.path),
            revspec,
            result.working_tree,
            normalize_extensions(tuple(extensions or ())),
            result.riskignore_excludes + result.request_excludes,
            (changed, changed_error),
            include_paths,
        )
        patch = await _patch_coverage_block(
            ctx, patch_changed, patch_error, patch_revspec, result, collector, label=patch_label
        )
    if patch is not None:
        payload["patch_coverage"] = patch
    with _timed(timings, "prior_fixes"):
        prior_fixes = await _prior_fixes_block(ctx, changed, history_ref=result.history_ref)
    if prior_fixes is not None:
        # One fix record, not two. The blocks answered the same question
        # with different arithmetic (decayed pressure vs raw count) and a
        # reader had to work out which was which.
        payload.setdefault("fix_history", {})["overlap"] = prior_fixes
    alias = getattr(ctx, "alias", "")
    # The join needs an open index per consumer repo, so it runs once for
    # the whole change and the block distributes its rows.
    with _timed(timings, "cross_repo"):
        impact = await _pkg.cross_repo_tests(alias, sorted(changed))
    cross_repo = _cross_repo_block(alias, sorted(changed), impact, collector)
    if cross_repo is not None:
        payload["cross_repo"] = cross_repo


async def _attach_branch_blocks(
    ctx: Any,
    payload: dict,
    changed: Any,
    collector: OmissionCollector,
    revspec: str | None,
    scan: asyncio.Task | None,
) -> Any:
    """Attach ``branch_overlap`` and return the independent-changes block, or None."""
    overlap = await _branch_overlap_block(
        ctx, changed, collector, revspec_head(revspec), scan=scan
    )
    if overlap is not None:
        payload["branch_overlap"] = overlap
    return await _independent_changes_block(ctx, changed, collector, revspec)


#: ``_meta`` keys a cached payload keeps; the rest is rebuilt on every serve.
_CARRIED_META = frozenset({"omitted", "recovery_unavailable", "replaced_tokens"})


@mcp.tool(
    surface_order=60,
    artifact_type="change_risk",
    presentation="change_risk",
    evidence_basis="measured",
)
@ends_call_tasks
async def get_change_risk(
    revspec: str | None = None,
    repo: str | None = None,
    extensions: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    include_paths: list[str] | str | None = None,
    baseline: int = 200,
    include: list[str] | None = None,
    finding_id: str | None = None,
) -> dict:
    """Review a commit, ``base..head`` range, or uncommitted work.

    Leads with ``directive`` (what to do) and ``health_delta`` (what this
    change newly made worse). Findings are reported only when the diff explains
    them, name their ``attribution`` basis, and sort change-written above
    pre-existing.

    ``directive.status`` is the verdict; ``partial`` health means code files
    were skipped. ``risk_percentile`` and ``diff_shape`` measure diff size
    only, not danger.

    ``impacted_tests`` is the ``impacted-tests`` selection; read ``status``.
    ``patch_coverage`` is the share of changed executable lines stored coverage
    ran; ``hints`` name tests to extend. ``fix_history`` is the changed files'
    bug-fix record, ``overlap`` the past fixes on these exact lines,
    ``branch_overlap`` other branches editing them. An empty diff returns
    ``status: "nothing_to_score"``.

    Args:
        revspec: Commit or ``base..head`` range. Omit to review uncommitted
            work, or ``HEAD`` when the tree is clean.
        repo: Repository alias in workspace mode; omit for the default.
        extensions: File suffixes to count, e.g. ``[".py", ".ts"]``.
        exclude_patterns: Gitignore-style paths to omit, e.g. ``["tests/"]``.
        include_paths: Gitignore-style paths to keep, as a list or one
            comma-separated string, e.g. ``"src/api/,src/db/"``. Omit for all.
        baseline: Recent commits sampled for percentile ranking; 0 disables it.
        include: ``"findings"``, ``"diagnostics"`` (raw mechanics),
            ``"scales"``, ``"evidence"`` or ``"tests"`` (why each runs).
        finding_id: Expand one ``health_delta`` finding by its id.
    """
    if repo == "all":
        return _unsupported_repo_all("get_change_risk")
    ctx = await _pkg._resolve_repo_context(repo)
    started = time.perf_counter()
    includes = _path_patterns(include_paths)
    key = await _cache.cache_key(ctx, revspec, extensions, exclude_patterns, includes, baseline)
    cached, rescan = await _from_cache(ctx, key, include, finding_id, revspec, started)
    if cached is not None:
        return cached
    # Per-block wall times, served only under the debug meta switch.
    timings: dict[str, int] = {}
    early = await _early_delta(ctx, revspec, extensions, exclude_patterns, includes, timings)
    result, error = await _score_beside_selection(
        ctx, revspec, finding_id, timings, extensions, exclude_patterns, includes, baseline
    )
    if error is not None or result.features.nf == 0:
        if early is not None:
            early[1].cancel()
        if error is not None:
            return error
    ignored: list[dict] = []
    include_set = _include_blocks(include, ignored)
    payload, diagnostics = _shape_payload(result, include_set, revspec)
    scored = await asyncio.to_thread(_scored_repo, str(ctx.path), revspec, result.working_tree)
    if result.features.nf == 0:
        return await _nothing_to_score(ctx, payload["ref"], scored, started)
    payload = {"ref": payload.pop("ref"), "scored_repo": scored, **payload}
    # Changed lines over the SAME file universe the score counted (its
    # extensions, includes, riskignore + request excludes), so nothing downstream
    # disagrees with the score about which files the change touches. Read once
    # and shared: both blocks below need it and git is the expensive part.
    # The test and fix blocks need the index and the cross-repo block needs
    # workspace contracts, but the branch-overlap block needs only git, so every
    # call pays this read rather than an unindexed repo going blind.
    changed, changed_error = await _changed_in_scope(
        str(ctx.path),
        revspec,
        normalize_extensions(tuple(extensions or ())),
        result.riskignore_excludes + result.request_excludes,
        includes,
        working_tree=result.working_tree,
    )
    collector = OmissionCollector("get_change_risk", repo_root=ctx.path)
    # The delta is the expensive half and needs nothing the enrichments need,
    # so it runs alongside them rather than after.
    delta_task = _delta_for(early, ctx, revspec, extensions, result, includes, timings)
    # A drill-down returns one finding and nothing else, so the tests,
    # coverage, fix, cross-repo and branch blocks would never reach the caller.
    scan = branches = None
    if finding_id is None:
        scan, branches = rescan or await _branch_scan(ctx, key, changed, revspec)
    independent = None
    # A block that raises leaves the comparison and the scan to ends_call_tasks.
    if finding_id is None:
        await _pkg._attach_change_blocks(
            ctx,
            payload,
            result,
            revspec,
            extensions,
            (changed, changed_error),
            collector,
            includes,
            tests_detail="tests" in include_set,
            timings=timings,
        )
        with _timed(timings, "branch_overlap"):
            independent = await _attach_branch_blocks(
                ctx, payload, changed, collector, revspec, scan
            )
    delta = await delta_task
    await _attach_health_references(ctx, delta)
    scored_entry = _cache.remember(key, payload["ref"], delta)
    if finding_id is not None:
        return _drill_down(payload, delta, finding_id, revspec)
    payload["diff_shape"] = _diff_shape_sentence(payload, diagnostics)
    if independent is not None:
        payload["independent_changes"] = independent
    _attach_health(
        payload,
        delta,
        revspec,
        expand="findings" in include_set,
        diagnostics="diagnostics" in include_set,
    )
    if "evidence" in include_set:
        payload["evidence"] = await _evidence_block(
            ctx,
            revspec,
            result,
            delta,
            tuple(extensions or ()),
            tuple(result.riskignore_excludes + result.request_excludes),
            scoped=bool(extensions or exclude_patterns or includes or baseline != 200),
        )
    # source: live_git marks that the *score* is computed from the working
    # checkout's git. The two blocks above are index-backed, so the freshness
    # fields do apply to them, scoped to the change's files. None (not []) when
    # there are none, so a degraded read keeps the repo-level staleness warning
    # instead of reading as "nothing served, nothing to warn about".
    payload["_meta"] = _pkg._build_meta(
        timing_ms=(time.perf_counter() - started) * 1000,
        repository=await _repository(ctx),
        targets=sorted(changed) or None,
        extra={"source": "live_git", **_debug_timings(timings)},
    )
    # The changed-file count is not on the default response, so the savings
    # counterfactual is declared here, where the score still holds it.
    declare_replaced(payload, change_risk_replaced_tokens(result.features.nf))
    attach_ignored_arguments(payload, ignored)
    collector.attach(payload)
    if scored_entry is not None:
        scored_entry.payloads[_cache.payload_key(include)] = _cache.Stored(
            copy.deepcopy(payload),
            sorted(changed) or None,
            branches,
            await _scan_answer(scan),
        )
    return payload


async def _branch_scan(
    ctx: Any, key: str | None, changed: Any, revspec: str | None
) -> tuple[asyncio.Task | None, str | None]:
    """``(the branch scan, the branch inputs it starts from)``; no inputs when uncached."""
    # Read before the scan, so tips that move during it fail the next check.
    branches = await asyncio.to_thread(_cache.branch_inputs, str(ctx.path)) if key else None
    return _start_overlap_scan(ctx, changed, revspec_head(revspec)), branches


async def _from_cache(
    ctx: Any,
    key: str | None,
    include: list[str] | None,
    finding_id: str | None,
    revspec: str | None,
    started: float,
) -> tuple[dict | None, tuple[asyncio.Task | None, str | None] | None]:
    """A reply from the range cache, else ``(None, a branch scan the rebuild can reuse)``."""
    hit = _cache.lookup(key)
    if hit is None:
        return None, None
    if finding_id is not None:
        return _drill_down({"ref": hit.ref}, hit.delta, finding_id, revspec), None
    stored = hit.payloads.get(_cache.payload_key(include))
    if stored is None:
        return None, None
    return await _revalidated(ctx, stored, revspec, started)


async def _revalidated(
    ctx: Any, stored: _cache.Stored, revspec: str | None, started: float
) -> tuple[dict | None, tuple[asyncio.Task | None, str | None] | None]:
    """``(payload, None)`` while its branch answer holds, else ``(None, (scan, inputs))``.

    Unmoved tips serve it as is. Moved tips rerun the scan, and an unchanged
    answer still serves it; a changed one hands the scan, and the inputs read
    before it, to the full rebuild.
    """
    branches = await asyncio.to_thread(_cache.branch_inputs, str(ctx.path))
    if branches is None or branches != stored.branches:
        scan = _start_overlap_scan(
            ctx, dict.fromkeys(stored.targets or ()), revspec_head(revspec)
        )
        answer = await _scan_answer(scan)
        # A failed scan is no answer, so it never vouches for the stored one.
        if answer != stored.overlap or (answer is None and stored.targets):
            return None, (scan, branches)
        stored.branches = branches
    return await _served(ctx, stored, started), None


async def _served(ctx: Any, stored: _cache.Stored, started: float) -> dict:
    """A cached payload with its ``_meta`` rebuilt, since freshness reads the live checkout."""
    targets = stored.targets
    out = copy.deepcopy(stored.payload)
    # Omission refs belong to the stored blocks, so they carry over.
    kept = {k: v for k, v in out.get("_meta", {}).items() if k in _CARRIED_META}
    out["_meta"] = {
        **_pkg._build_meta(
            timing_ms=(time.perf_counter() - started) * 1000,
            repository=await _repository(ctx),
            targets=targets,
            extra={"source": "live_git", "cached": True},
        ),
        **kept,
    }
    return out


async def _nothing_to_score(ctx: Any, ref: str, scored: dict[str, Any], started: float) -> dict:
    """An empty diff, reported as one rather than scored.

    A score would read as a clean bill of health on a tree the caller may not
    have meant; naming the tree makes a worktree mismatch visible.
    """
    return {
        "ref": ref,
        "status": "nothing_to_score",
        "warning": (
            f"No counted file changes in {ref!r} "
            "(check the revspec, extensions, or include/exclude filters)."
        ),
        "scored_repo": scored,
        "_meta": _pkg._build_meta(
            timing_ms=(time.perf_counter() - started) * 1000,
            repository=await _repository(ctx),
            extra={"source": "live_git"},
        ),
    }

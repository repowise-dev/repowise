"""MCP tool for live commit and range change-risk scoring."""

from __future__ import annotations

import asyncio
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
from repowise.server.mcp_server._helpers import (
    _unsupported_repo_all,
    attach_ignored_arguments,
)

from .branches import _branch_overlap_block, _independent_changes_block
from .changed import _changed_in_scope, _push_change
from .cross_repo import _cross_repo_block
from .health import (
    _attach_health_references,
    _delta_with_index,
    _evidence_block,
    _repository,
    _scored_repo,
)
from .impacted import _impacted_tests_block
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
        detail = (exc.stderr or "").strip() or str(exc)
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


async def _attach_change_blocks(
    ctx: Any,
    payload: dict,
    result: Any,
    revspec: str | None,
    extensions: list[str] | None,
    changed_read: tuple[Any, Any],
    collector: OmissionCollector,
    include_paths: tuple[str, ...] = (),
) -> None:
    """Attach the tests, patch coverage, prior fixes and cross-repo blocks, in that order."""
    changed, changed_error = changed_read
    payload["impacted_tests"] = await _impacted_tests_block(
        ctx,
        changed,
        changed_error,
        collector,
        revspec=revspec,
        working_tree=result.working_tree,
        owned_excludes=result.owned_excludes,
    )
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
    prior_fixes = await _prior_fixes_block(ctx, changed, history_ref=result.history_ref)
    if prior_fixes is not None:
        # One fix record, not two. The blocks answered the same question
        # with different arithmetic (decayed pressure vs raw count) and a
        # reader had to work out which was which.
        payload.setdefault("fix_history", {})["overlap"] = prior_fixes
    alias = getattr(ctx, "alias", "")
    # The join needs an open index per consumer repo, so it runs once for
    # the whole change and the block distributes its rows.
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
    finding_id: str | None,
) -> Any:
    """Attach ``branch_overlap`` and return the independent-changes block, or None."""
    # A drill-down returns one finding and nothing else, so neither block
    # would reach the caller; both are pure cost on that path.
    if finding_id is not None:
        return None
    overlap = await _branch_overlap_block(ctx, changed, collector, revspec_head(revspec))
    if overlap is not None:
        payload["branch_overlap"] = overlap
    return await _independent_changes_block(ctx, changed, collector, revspec)


@mcp.tool(
    surface_order=60,
    artifact_type="change_risk",
    presentation="change_risk",
    evidence_basis="measured",
)
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

    ``impacted_tests`` is the ``impacted-tests`` selection; read ``run_all``.
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
            ``"scales"`` (units, tercile) or ``"evidence"`` (checklist).
        finding_id: Expand one ``health_delta`` finding by its id.
    """
    if repo == "all":
        return _unsupported_repo_all("get_change_risk")
    ctx = await _pkg._resolve_repo_context(repo)
    started = time.perf_counter()
    includes = _path_patterns(include_paths)
    result, error = await _score_change(
        ctx.path, revspec, extensions, exclude_patterns, includes, baseline
    )
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
    delta_task = asyncio.create_task(
        _delta_with_index(
            ctx,
            revspec,
            tuple(extensions or ()),
            tuple(result.riskignore_excludes + result.request_excludes),
            includes,
        )
    )
    try:
        # A drill-down returns one finding and nothing else, so the tests,
        # coverage, fix and cross-repo blocks would never reach the caller.
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
            )
        independent = await _attach_branch_blocks(
            ctx, payload, changed, collector, revspec, finding_id
        )
    except BaseException:
        # Never leave the comparison running for a request that is already over.
        delta_task.cancel()
        raise
    delta = await delta_task
    await _attach_health_references(ctx, delta)
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
        extra={"source": "live_git"},
    )
    attach_ignored_arguments(payload, ignored)
    collector.attach(payload)
    return payload


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

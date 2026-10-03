"""MCP Tool 7: get_dead_code — tiered refactor plan for unused code."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.dead_code.models import DeadCodeKind
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
from repowise.core.analysis.dead_code.serving import (
    TIER_FLOORS,
    CrossRepoLookup,
    FindingFilters,
    adjust_cross_repo,
    apply_filters,
    build_summary,
    build_tiers,
    compute_impact,
    dead_code_finding_id,
    excluded_kinds,
    merge_summary_counts,
    rollup_by_directory,
    rollup_by_owner,
    serialize_finding,
    summary_counts,
)
from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.persistence.crud import get_dead_code_findings
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GitMetadata
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._basis import call_resolution_bases
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _is_workspace_mode,
    _resolve_all_contexts,
    _resolve_repo_context,
    attach_ignored_arguments,
    filter_rows_by_attr,
    resolve_enum_argument,
)
from repowise.server.mcp_server._index_state import index_state_key
from repowise.server.mcp_server._meta import build_meta as _build_meta


def _cross_repo_lookup() -> CrossRepoLookup | None:
    """The workspace's cross-repo data, when there is any to adjust by."""
    enricher = _state._cross_repo_enricher
    if enricher is None or not enricher.has_data or not _is_workspace_mode():
        return None
    return enricher


async def _get_dead_code_all_repos(
    filters: FindingFilters,
    limit: int,
    tier: str | None,
    apply_limit_note: Callable[[dict[str, Any]], None],
) -> dict:
    """Aggregate dead-code findings across every repo in the workspace."""
    contexts = await _resolve_all_contexts()
    merged_findings: list[dict] = []
    count_parts: list[dict[str, Any]] = []
    merged_withheld: dict[str, dict] = {}

    for ctx in contexts:
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)

            repo_findings = filter_rows_by_attr(
                await get_dead_code_findings(session, repository.id, include_withheld=True),
                "file_path",
                _get_exclude_spec(ctx.path),
            )
            repo_findings, repo_withheld = filters.split_withheld(repo_findings)
            for name, entry in repo_withheld.items():
                merged = merged_withheld.setdefault(name, {**entry, "count": 0})
                merged["count"] += entry["count"]

            git_meta_map = await _load_git_meta_map(session, repository.id, repo_findings)

        for f in apply_filters(repo_findings, filters):
            serialized = serialize_finding(f, git_meta_map, repository=ctx.alias)
            serialized["repo"] = ctx.alias
            merged_findings.append(serialized)
        count_parts.append(summary_counts(repo_findings))

    summary = build_summary(
        merge_summary_counts(count_parts), len(merged_findings), filters, merged_withheld
    )
    tiers = build_tiers(merged_findings, limit, tier)
    adjust_cross_repo(tiers, _cross_repo_lookup())

    result_ws: dict[str, Any] = {
        "workspace": True,
        "summary": summary,
        "tiers": tiers,
        "impact": compute_impact(tiers),
    }
    apply_limit_note(result_ws)
    result_ws["_meta"] = _build_meta()
    return result_ws


# The four kinds the analyzer writes, taken from the enum it writes them with
# rather than re-listed here, so a fifth kind never reads as a caller's typo.
_DEAD_CODE_KINDS = frozenset(k.value for k in DeadCodeKind)


async def _load_git_meta_map(session: Any, repository_id: Any, findings: list) -> dict[str, Any]:
    """Load git metadata keyed by file path for the given findings."""
    finding_paths = list({f.file_path for f in findings})
    if not finding_paths:
        return {}
    git_res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path.in_(finding_paths),
        )
    )
    return {g.file_path: g for g in git_res.scalars().all()}


def _resolve_min_confidence(value: float | str, ignored: list[dict[str, Any]]) -> float:
    """Resolve ``min_confidence`` to a float, accepting this tool's tier names.

    The response is organised by ``high`` / ``medium`` / ``low`` and each tier
    description states its band, so those are the words a caller reaches for;
    the parameter used to reject them outright (#1496). A numeric string is
    accepted too, because widening the annotation to ``float | str`` is what
    lets one through in the first place.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        return float(value)
    name = value.strip()
    if name in TIER_FLOORS:
        return TIER_FLOORS[name]
    try:
        return float(name)
    except ValueError:
        resolve_enum_argument(name, TIER_FLOORS, argument="min_confidence", ignored=ignored)
        return RISK_CAP_CONFIDENCE


@mcp.tool(surface_order=100, artifact_type="dead_code", presentation="dead_code", evidence_basis="inferred")
async def get_dead_code(
    repo: str | None = None,
    kind: str | None = None,
    min_confidence: float | str = RISK_CAP_CONFIDENCE,
    safe_only: bool = False,
    limit: int = 20,
    tier: str | None = None,
    directory: str | None = None,
    owner: str | None = None,
    group_by: str | None = None,
    include_internals: bool = False,
    include_zombie_packages: bool = True,
    no_unreachable: bool = False,
    no_unused_exports: bool = False,
    finding_id: str | None = None,
) -> dict:
    """Unused exports, unreachable files, zombie packages — tiered by confidence.

    Run before a cleanup sprint, not a targeted fix. Findings tier
    high/medium/low with per-directory and per-owner rollups; workspace
    mode lowers confidence on findings other repos import.

    Args:
        repo: usually omitted.
        kind: unreachable_file | unused_export | unused_internal | zombie_package.
            An unrecognised value is dropped and named in ignored_arguments,
            never applied as a filter that matches nothing.
        min_confidence: floor, default 0.4 (0.7 = cleanup-ready only). Also
            accepts a tier name: "high" | "medium" | "low".
        safe_only: deletion-ready findings only (no runtime-load risk).
        limit: max findings per tier (clamped to 25).
        tier: "high" | "medium" | "low", banded as min_confidence.
        directory: path-prefix filter.
        owner: primary-owner filter.
        group_by: "directory" | "owner" rollup.
        include_internals: also scan private symbols (more false positives).
        include_zombie_packages: monorepo package findings (default true).
        no_unreachable: skip file-level reachability findings.
        no_unused_exports: skip public-export findings.
        finding_id: stable ``id`` emitted by a dead-code finding.
    """
    # MCP transport rejects payloads above ~25k tokens. A single serialized
    # finding is ~400 chars, so 3 tiers x ~25 findings keeps us under budget
    # with headroom for summary/grouping fields.
    max_per_tier = 25
    requested_limit = limit
    limit = min(max(limit, 1), max_per_tier)
    limit_clamped = requested_limit > max_per_tier

    # Validated before anything is fetched: an unrecognised kind or tier is
    # dropped rather than filtered on, so the answer is the unfiltered one plus
    # a note, not "No dead code found matching your filters" over 445 findings.
    ignored: list[dict[str, Any]] = []
    kind = resolve_enum_argument(kind, _DEAD_CODE_KINDS, argument="kind", ignored=ignored)
    tier = resolve_enum_argument(tier, TIER_FLOORS, argument="tier", ignored=ignored)
    confidence_floor = _resolve_min_confidence(min_confidence, ignored)

    filters = FindingFilters(
        kind=kind,
        safe_only=safe_only,
        min_confidence=confidence_floor,
        directory=directory,
        owner=owner,
        excluded_kinds=excluded_kinds(
            no_unreachable=no_unreachable,
            no_unused_exports=no_unused_exports,
            include_internals=include_internals,
            include_zombie_packages=include_zombie_packages,
        ),
        # Naming a kind, or ``include_internals`` for ``unused_internal``, is the
        # explicit request a provisional kind needs; a hidden kind stays out.
        withheld_kinds=excluded_types(
            requested=[k for k in (kind, "unused_internal" if include_internals else None) if k]
        ),
    )

    def _maybe_limit_note(target: dict[str, Any]) -> None:
        if limit_clamped:
            target["limit_note"] = (
                f"Requested limit={requested_limit} exceeded the MCP transport budget "
                f"and was clamped to {max_per_tier}. Use tier/directory/owner filters "
                "or paginate to see more findings."
            )

    # --- repo="all": aggregate dead code across all repos ---
    if repo == "all":
        if finding_id:
            for candidate_ctx in await _resolve_all_contexts():
                resolved = await get_dead_code(
                    repo=candidate_ctx.alias,
                    min_confidence="low",
                    finding_id=finding_id,
                )
                if resolved.get("resolved"):
                    resolved["workspace"] = True
                    return resolved
            return {
                "mode": "finding",
                "finding_id": finding_id,
                "finding": None,
                "resolved": False,
                "workspace": True,
                "_meta": _build_meta(),
            }
        result_ws = await _get_dead_code_all_repos(filters, limit, tier, _maybe_limit_note)
        attach_ignored_arguments(result_ws, ignored)
        return result_ws

    # --- Single repo path ---
    ctx = await _resolve_repo_context(repo)
    # Findings beyond the per-tier limit are persisted, not silently dropped —
    # the response carries an expandable [repowise#<ref>] marker for them.
    collector = OmissionCollector("get_dead_code", repo_root=ctx.path)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)

        # Fetch all open findings for summary computation. Withheld kinds are
        # read too, so the summary can count what it leaves out.
        all_findings, withheld = filters.split_withheld(
            await get_dead_code_findings(session, repository.id, include_withheld=True)
        )

        # Phase 4: load git metadata for "last meaningful change" enrichment
        git_meta_map = await _load_git_meta_map(session, repository.id, all_findings)

    reference_repository = ctx.alias or repository.name
    if finding_id:
        match = next(
            (
                row
                for row in all_findings
                if finding_id in {row.id, dead_code_finding_id(row, reference_repository)}
            ),
            None,
        )
        return {
            "mode": "finding",
            "finding_id": finding_id,
            "finding": (
                serialize_finding(
                    match,
                    git_meta_map,
                    repository=reference_repository,
                )
                if match
                else None
            ),
            "resolved": match is not None,
            "_meta": _build_meta(
                repository=repository,
                targets=[match.file_path] if match else None,
            ),
        }

    filtered = apply_filters(all_findings, filters)
    serialized = [
        serialize_finding(f, git_meta_map, repository=reference_repository) for f in filtered
    ]

    def _keep_overflow(name: str, beyond: list[dict]) -> None:
        collector.add(
            f"{name}-tier findings beyond limit={limit} ({len(beyond)} dropped)",
            "\n".join(json.dumps(f, separators=(",", ":")) for f in beyond),
        )

    tiers = build_tiers(serialized, limit, tier, on_overflow=_keep_overflow)
    summary = build_summary(summary_counts(all_findings), len(filtered), filters, withheld)
    adjust_cross_repo(tiers, _cross_repo_lookup(), ctx.alias)

    result: dict[str, Any] = {"summary": summary, "tiers": tiers}
    if group_by == "directory":
        result["by_directory"] = rollup_by_directory(filtered)
    elif group_by == "owner":
        result["by_owner"] = rollup_by_owner(filtered)
    result["impact"] = compute_impact(tiers)

    _maybe_limit_note(result)

    # How much of the call graph these findings rest on, per language. A
    # reachability finding is only as strong as the edges that reached.
    summary["call_resolution_basis"] = await call_resolution_bases(
        session, repository.id, cache_key=index_state_key(repository)
    )

    result["_meta"] = _build_meta(repository=repository)
    attach_ignored_arguments(result, ignored)
    collector.attach(result)
    return result

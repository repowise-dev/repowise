"""MCP Tool 7: get_dead_code — tiered refactor plan for unused code."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.dead_code.models import DeadCodeKind
from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    effective_safe_to_delete,
    path_risk_factors,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
)
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
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
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._references import path_identity, stable_entity_id


@dataclass
class _FindingFilters:
    """Filter parameters shared by the single-repo and workspace code paths."""

    kind: str | None
    safe_only: bool
    min_confidence: float
    directory: str | None
    owner: str | None
    excluded_kinds: set[str] = field(default_factory=set)


def _compute_excluded_kinds(
    *,
    no_unreachable: bool,
    no_unused_exports: bool,
    include_internals: bool,
    include_zombie_packages: bool,
) -> set[str]:
    """Derive the set of finding kinds to exclude from the scope flags."""
    excluded: set[str] = set()
    if no_unreachable:
        excluded.add("unreachable_file")
    if no_unused_exports:
        excluded.add("unused_export")
    if not include_internals:
        excluded.add("unused_internal")
    if not include_zombie_packages:
        excluded.add("zombie_package")
    return excluded


def _apply_finding_filters(findings: list, filters: _FindingFilters) -> list:
    """Apply the kind/safety/confidence/directory/owner filters in order."""
    filtered = findings
    if filters.kind:
        filtered = [f for f in filtered if f.kind == filters.kind]
    elif filters.excluded_kinds:
        filtered = [f for f in filtered if f.kind not in filters.excluded_kinds]
    if filters.safe_only:
        filtered = [f for f in filtered if _effective_safe(f)]
    if filters.min_confidence > 0:
        filtered = [f for f in filtered if f.confidence >= filters.min_confidence]
    if filters.directory:
        prefix = filters.directory.rstrip("/") + "/"
        filtered = [f for f in filtered if f.file_path.startswith(prefix)]
    if filters.owner:
        owner_lower = filters.owner.lower()
        filtered = [
            f for f in filtered if f.primary_owner and f.primary_owner.lower() == owner_lower
        ]
    return filtered


async def _get_dead_code_all_repos(
    filters: _FindingFilters,
    limit: int,
    tier: str | None,
    apply_limit_note: Callable[[dict[str, Any]], None],
) -> dict:
    """Aggregate dead-code findings across every repo in the workspace."""
    contexts = await _resolve_all_contexts()
    merged_findings: list[dict] = []
    total_all = 0
    total_deletable = 0
    total_safe = 0
    merged_by_kind: dict[str, int] = {}

    for ctx in contexts:
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)

            all_query = select(DeadCodeFinding).where(
                DeadCodeFinding.repository_id == repository.id,
                DeadCodeFinding.status == "open",
            )
            all_result = await session.execute(all_query)
            repo_findings = filter_rows_by_attr(
                list(all_result.scalars().all()), "file_path", _get_exclude_spec(ctx.path)
            )

            git_meta_map = await _load_git_meta_map(session, repository.id, repo_findings)

        repo_filtered = _apply_finding_filters(repo_findings, filters)

        for f in repo_filtered:
            serialized = _serialize_finding(f, git_meta_map, repository=ctx.alias)
            serialized["repo"] = ctx.alias
            merged_findings.append(serialized)

        # Accumulate summary stats from unfiltered findings
        total_all += len(repo_findings)
        total_deletable += sum(f.lines for f in repo_findings if _effective_safe(f))
        total_safe += sum(1 for f in repo_findings if _effective_safe(f))
        for f in repo_findings:
            merged_by_kind[f.kind] = merged_by_kind.get(f.kind, 0) + 1

    # Sort merged findings by confidence descending
    merged_findings.sort(key=lambda d: (-d["confidence"], -d["lines"]))

    summary = {
        "total_findings": total_all,
        "filtered_findings": len(merged_findings),
        "deletable_lines": total_deletable,
        "safe_to_delete_count": total_safe,
        "by_kind": merged_by_kind,
    }

    tiers = _build_tiers_from_dicts(merged_findings, limit, tier)

    # Cross-repo confidence adjustment for workspace-wide findings
    _adjust_dead_code_cross_repo(tiers, None)

    result_ws: dict[str, Any] = {
        "workspace": True,
        "summary": summary,
        "tiers": tiers,
        "impact": _compute_impact(tiers),
    }
    apply_limit_note(result_ws)
    result_ws["_meta"] = _build_meta()
    return result_ws


# The bands this tool tiers by, in one place: the tier descriptions quote them
# and ``min_confidence="high"`` resolves to them, so the vocabulary the response
# is organised by and the one it accepts cannot drift apart (#1496). Deliberately
# this tool's own numbers — the web and CLI tier at 0.7/0.4
# (``DEAD_CODE_CONFIDENCE`` in packages/types/src/dead-code.ts); reconciling the
# two changes output and is not this change.
_TIER_FLOORS: dict[str, float] = {"high": 0.8, "medium": 0.5, "low": 0.0}

# The four kinds the analyzer writes, taken from the enum it writes them with
# rather than re-listed here, so a fifth kind never reads as a caller's typo.
_DEAD_CODE_KINDS = frozenset(k.value for k in DeadCodeKind)


def _build_tiers_from_dicts(
    merged_findings: list[dict], limit: int, tier: str | None
) -> dict[str, Any]:
    """Build the high/medium/low tier structure from pre-serialized dicts."""
    hi, med = _TIER_FLOORS["high"], _TIER_FLOORS["medium"]
    high = [f for f in merged_findings if f["confidence"] >= hi]
    medium = [f for f in merged_findings if med <= f["confidence"] < hi]
    low = [f for f in merged_findings if f["confidence"] < med]

    def _tier_from_dicts(items: list[dict], desc: str) -> dict:
        return {
            "description": desc,
            "count": len(items),
            "lines": sum(f["lines"] for f in items),
            "safe_count": sum(1 for f in items if f["safe_to_delete"]),
            "findings": items[:limit],
            "truncated": len(items) > limit,
        }

    tiers: dict[str, Any] = {}
    if tier is None or tier == "high":
        tiers["high"] = _tier_from_dicts(high, _TIER_DESC_HIGH)
    if tier is None or tier == "medium":
        tiers["medium"] = _tier_from_dicts(medium, _TIER_DESC_MEDIUM)
    if tier is None or tier == "low":
        tiers["low"] = _tier_from_dicts(low, _TIER_DESC_LOW)
    return tiers


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


_TIER_DESC_HIGH = (
    "High confidence (>=0.8): No references found in the codebase. "
    "Strong cleanup candidates — review (especially runtime-loaded files) before deleting."
)
_TIER_DESC_MEDIUM = (
    "Medium confidence (0.5-0.8): Likely unused but may have indirect references. "
    "Review before deleting."
)
_TIER_DESC_LOW = (
    "Low confidence (<0.5): Potentially used via dynamic imports or reflection. Investigate first."
)


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
    if name in _TIER_FLOORS:
        return _TIER_FLOORS[name]
    try:
        return float(name)
    except ValueError:
        resolve_enum_argument(name, _TIER_FLOORS, argument="min_confidence", ignored=ignored)
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
            accepts a tier name: "high" (0.8) | "medium" (0.5) | "low" (0.0).
        safe_only: deletion-ready findings only (no runtime-load risk).
        limit: max findings per tier (clamped to 25).
        tier: "high" (>=0.8) | "medium" | "low".
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
    tier = resolve_enum_argument(tier, _TIER_FLOORS, argument="tier", ignored=ignored)
    confidence_floor = _resolve_min_confidence(min_confidence, ignored)

    filters = _FindingFilters(
        kind=kind,
        safe_only=safe_only,
        min_confidence=confidence_floor,
        directory=directory,
        owner=owner,
        excluded_kinds=_compute_excluded_kinds(
            no_unreachable=no_unreachable,
            no_unused_exports=no_unused_exports,
            include_internals=include_internals,
            include_zombie_packages=include_zombie_packages,
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

        # Fetch all open findings for summary computation
        all_query = select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository.id,
            DeadCodeFinding.status == "open",
        )
        all_result = await session.execute(all_query)
        all_findings = list(all_result.scalars().all())

        # Phase 4: load git metadata for "last meaningful change" enrichment
        git_meta_map = await _load_git_meta_map(session, repository.id, all_findings)

    reference_repository = ctx.alias or repository.name
    if finding_id:
        match = next(
            (
                row
                for row in all_findings
                if finding_id
                in {row.id, _dead_code_finding_id(row, reference_repository)}
            ),
            None,
        )
        return {
            "mode": "finding",
            "finding_id": finding_id,
            "finding": (
                _serialize_finding(
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

    # --- Apply filters ---
    filtered = _apply_finding_filters(all_findings, filters)

    # --- Build tiered structure ---
    tiers = _build_tiers(
        filtered,
        limit,
        tier,
        git_meta_map,
        collector,
        repository=reference_repository,
    )

    # --- Summary across ALL open findings (unfiltered) ---
    by_kind: dict[str, int] = {}
    for f in all_findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    summary = {
        "total_findings": len(all_findings),
        "filtered_findings": len(filtered),
        "deletable_lines": sum(f.lines for f in all_findings if _effective_safe(f)),
        "safe_to_delete_count": sum(1 for f in all_findings if _effective_safe(f)),
        "by_kind": by_kind,
    }

    # Cross-repo confidence adjustment (Phase 3)
    _adjust_dead_code_cross_repo(tiers, ctx.alias)

    result: dict[str, Any] = {"summary": summary, "tiers": tiers}

    # --- Grouping views ---
    if group_by == "directory":
        result["by_directory"] = _rollup_by_directory(filtered)
    elif group_by == "owner":
        result["by_owner"] = _rollup_by_owner(filtered)

    # --- Impact estimate ---
    result["impact"] = _compute_impact(tiers)

    _maybe_limit_note(result)

    result["_meta"] = _build_meta(repository=repository)
    attach_ignored_arguments(result, ignored)
    collector.attach(result)
    return result


def _adjust_dead_code_cross_repo(tiers: dict, repo_alias: str | None) -> None:
    """Reduce confidence for dead code findings that have cross-repo consumers.

    Mutates findings in-place within the tier dicts.
    """
    enricher = _state._cross_repo_enricher
    if enricher is None or not enricher.has_data or not _is_workspace_mode():
        return

    for tier_data in tiers.values():
        for finding in tier_data.get("findings", []):
            alias = finding.get("repo", repo_alias)
            if not alias:
                continue
            file_path = finding.get("file_path", "")
            consumers = enricher.has_cross_repo_consumers(alias, file_path)
            if consumers:
                original = finding["confidence"]
                finding["confidence"] = round(original * 0.5, 2)
                consumer_repos = sorted(set(c["repo"] for c in consumers))
                finding["cross_repo_note"] = (
                    f"Confidence reduced: {len(consumers)} cross-repo consumer(s) "
                    f"in {', '.join(consumer_repos)}."
                )
            # Also check if other repos depend on this repo via package deps
            if alias and finding.get("kind") == "unused_export":
                depending = enricher.get_repos_depending_on(alias)
                if depending and not consumers:
                    original = finding["confidence"]
                    finding["confidence"] = round(original * 0.3, 2)
                    finding["cross_repo_note"] = (
                        f"This export may be consumed by: {', '.join(depending)}. "
                        "Verify before deletion."
                    )


def _find_last_meaningful_change(gm: Any) -> str | None:
    """Find the date of the last feature/fix commit (not style/chore) from git metadata."""
    if gm is None:
        return None
    sig_json = getattr(gm, "significant_commits_json", None)
    _cat_json = getattr(gm, "commit_categories_json", None)
    # If we have significant commits, the most recent one is the best proxy
    # for "last meaningful change" (significant commits already filter noise)
    if sig_json:
        try:
            commits = json.loads(sig_json)
            if commits:
                return commits[0].get("date")  # most recent first
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _effective_safe(f: Any) -> bool:
    """Re-derive deletion-readiness for a DeadCodeFinding ORM row.

    Mirrors the API/CLI: the persisted boolean is only ever downgraded, never
    trusted blindly, so config/bootstrap/database/environment/script/asset
    files (and findings written before risk factors existed) never read as
    safe-to-delete.
    """
    return effective_safe_to_delete(f.confidence, f.file_path, f.safe_to_delete)


def _dead_code_finding_id(f: Any, repository: str) -> str:
    return stable_entity_id(
        "finding",
        repository,
        {
            "family": "dead_code",
            "path": path_identity(f.file_path),
            "kind": f.kind,
            "symbol": f.symbol_name or "",
            "line_start": f.start_line,
            "line_end": f.end_line,
        },
    )


def _serialize_finding(
    f: Any,
    git_meta_map: dict | None = None,
    *,
    repository: str = "default",
) -> dict:
    """Serialize a single DeadCodeFinding to dict."""
    result = {
        "id": _dead_code_finding_id(f, repository),
        "repository": repository,
        "kind": f.kind,
        "file_path": f.file_path,
        "symbol_name": f.symbol_name,
        "symbol_kind": f.symbol_kind,
        "start_line": f.start_line,
        "end_line": f.end_line,
        "confidence": f.confidence,
        "reason": f.reason,
        "safe_to_delete": _effective_safe(f),
        "risk_factors": list(path_risk_factors(f.file_path)),
        "lines": f.lines,
        "last_commit_at": f.last_commit_at.isoformat() if f.last_commit_at else None,
        # Recent churn is the top rung of the confidence ladder, so the agent
        # gets the same reason for a low score that the web page now shows.
        "commit_count_90d": f.commit_count_90d,
        "primary_owner": f.primary_owner,
        # NB: age_days runs from the file's *first* commit, so it is the file's
        # age, not how long this has been dead — the two disagree on 75% of
        # findings. last_commit_at above is the staleness signal.
        "age_days": f.age_days,
    }
    # Phase 4: add last meaningful change date
    if git_meta_map:
        gm = git_meta_map.get(f.file_path)
        meaningful = _find_last_meaningful_change(gm)
        if meaningful:
            result["last_meaningful_change"] = meaningful
    return result


def _build_tiers(
    findings: list,
    limit: int,
    tier_filter: str | None,
    git_meta_map: dict | None = None,
    collector: OmissionCollector | None = None,
    *,
    repository: str = "default",
) -> dict:
    """Split findings into high/medium/low confidence tiers.

    With a *collector*, findings beyond the per-tier limit are captured for
    the omission store instead of being silently truncated.
    """
    hi, med = _TIER_FLOORS["high"], _TIER_FLOORS["medium"]
    high = sorted(
        [f for f in findings if f.confidence >= hi],
        key=lambda f: (-f.confidence, -f.lines),
    )
    medium = sorted(
        [f for f in findings if med <= f.confidence < hi],
        key=lambda f: (-f.confidence, -f.lines),
    )
    low = sorted(
        [f for f in findings if f.confidence < med],
        key=lambda f: (-f.confidence, -f.lines),
    )

    def _tier_block(name: str, items: list, description: str) -> dict:
        beyond_limit = items[limit:]
        if beyond_limit and collector is not None:
            collector.add(
                f"{name}-tier findings beyond limit={limit} ({len(beyond_limit)} dropped)",
                "\n".join(
                    json.dumps(
                        _serialize_finding(f, git_meta_map, repository=repository),
                        separators=(",", ":"),
                    )
                    for f in beyond_limit
                ),
            )
        return {
            "description": description,
            "count": len(items),
            "lines": sum(f.lines for f in items),
            "safe_count": sum(1 for f in items if _effective_safe(f)),
            "findings": [
                _serialize_finding(f, git_meta_map, repository=repository)
                for f in items[:limit]
            ],
            "truncated": len(items) > limit,
        }

    tiers = {}
    if tier_filter is None or tier_filter == "high":
        tiers["high"] = _tier_block("high", high, _TIER_DESC_HIGH)
    if tier_filter is None or tier_filter == "medium":
        tiers["medium"] = _tier_block("medium", medium, _TIER_DESC_MEDIUM)
    if tier_filter is None or tier_filter == "low":
        tiers["low"] = _tier_block("low", low, _TIER_DESC_LOW)
    return tiers


def _rollup_by_directory(findings: list) -> list[dict]:
    """Group findings by top-level directory."""
    dirs: dict[str, dict] = {}
    for f in findings:
        parts = f.file_path.split("/")
        # Use first two path segments as directory key, or just the first
        dir_key = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
        if dir_key not in dirs:
            dirs[dir_key] = {"directory": dir_key, "count": 0, "lines": 0, "safe_count": 0}
        dirs[dir_key]["count"] += 1
        dirs[dir_key]["lines"] += f.lines
        if _effective_safe(f):
            dirs[dir_key]["safe_count"] += 1

    return sorted(dirs.values(), key=lambda d: -d["lines"])


def _rollup_by_owner(findings: list) -> list[dict]:
    """Group findings by primary owner."""
    owners: dict[str, dict] = {}
    for f in findings:
        name = f.primary_owner or "unowned"
        if name not in owners:
            owners[name] = {"owner": name, "count": 0, "lines": 0, "safe_count": 0}
        owners[name]["count"] += 1
        owners[name]["lines"] += f.lines
        if _effective_safe(f):
            owners[name]["safe_count"] += 1

    return sorted(owners.values(), key=lambda o: -o["lines"])


def _compute_impact(tiers: dict) -> dict:
    """Compute total impact across tiers."""
    total_lines = 0
    safe_lines = 0
    for tier_data in tiers.values():
        total_lines += tier_data["lines"]
        # Approximate safe lines from findings in the tier
        for f in tier_data["findings"]:
            if f["safe_to_delete"]:
                safe_lines += f["lines"]

    return {
        "total_lines_reclaimable": total_lines,
        "safe_lines_reclaimable": safe_lines,
        "recommendation": (
            "Start with the 'high' tier — these have no references in the graph and are the "
            "strongest cleanup candidates. Confirm each (runtime-loaded config/bootstrap/database "
            "files are flagged but never auto-marked safe), then review 'medium' tier with your team."
            if total_lines > 0
            else "No dead code found matching your filters."
        ),
    }

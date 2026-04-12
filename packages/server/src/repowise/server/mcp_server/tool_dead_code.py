"""MCP Tool 7: get_dead_code — tiered refactor plan for unused code."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
)
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_all_contexts,
    _resolve_repo_context,
)
from repowise.server.mcp_server._server import mcp


@mcp.tool()
async def get_dead_code(
    repo: str | None = None,
    kind: str | None = None,
    min_confidence: float = 0.5,
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
) -> dict:
    """Get a tiered refactor plan for dead and unused code.

    Returns findings organized into confidence tiers (high/medium/low),
    with per-directory rollups, ownership hotspots, and impact estimates
    so you can prioritize cleanup.

    Use group_by="directory" for a directory-level overview, or
    group_by="owner" to see who owns the most dead code. Use tier
    to focus on a single confidence band.

    Scan scope flags (mirror the DeadCodeAnalyzer.analyze config):
    - Use ``min_confidence=0.7`` for high-confidence cleanups — filters out
      speculative findings and surfaces only code with zero references that
      hasn't been touched in months. Ideal before a release or refactor sprint.
    - Use ``include_internals=True`` for aggressive scans of private symbols
      (functions/variables prefixed with _ or declared without exports). This
      has a higher false-positive rate and is off by default; enable it when
      doing a thorough dead-code purge of a stable, well-tested module.
    - Use ``no_unreachable=True`` to skip file-level reachability checks and
      focus only on symbol-level findings (unused exports/internals).
    - Use ``no_unused_exports=True`` to skip public-export checks, e.g. when
      you know the repo exposes a public API consumed externally.
    - Use ``include_zombie_packages=False`` to suppress monorepo package
      findings, useful in repos where cross-package imports are intentionally
      absent during development.

    Args:
        repo: Repository path, name, or ID.
        kind: Filter by kind (unreachable_file, unused_export, unused_internal, zombie_package).
        min_confidence: Minimum confidence threshold (default 0.5). Use 0.7 for high-confidence
            cleanups only.
        safe_only: Only return findings marked safe_to_delete (default false).
        limit: Maximum findings per tier (default 20).
        tier: Focus on a single tier: "high" (>=0.8), "medium" (0.5-0.8), or "low" (<0.5).
        directory: Filter findings to a specific directory prefix.
        owner: Filter findings by primary owner name.
        group_by: "directory" for per-directory rollup, "owner" for ownership hotspots.
        include_internals: Include unused private/internal symbol findings (default false).
            Enable for aggressive scans of private symbols.
        include_zombie_packages: Include zombie-package findings for monorepo packages with
            no external importers (default true).
        no_unreachable: Suppress unreachable-file findings (default false).
        no_unused_exports: Suppress unused-export findings (default false).
    """
    # --- repo="all": aggregate dead code across all repos ---
    if repo == "all":
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
                repo_findings = list(all_result.scalars().all())

                finding_paths = list({f.file_path for f in repo_findings})
                git_meta_map: dict[str, Any] = {}
                if finding_paths:
                    git_res = await session.execute(
                        select(GitMetadata).where(
                            GitMetadata.repository_id == repository.id,
                            GitMetadata.file_path.in_(finding_paths),
                        )
                    )
                    git_meta_map = {g.file_path: g for g in git_res.scalars().all()}

            # Apply scope-based exclusions
            _excluded_kinds: set[str] = set()
            if no_unreachable:
                _excluded_kinds.add("unreachable_file")
            if no_unused_exports:
                _excluded_kinds.add("unused_export")
            if not include_internals:
                _excluded_kinds.add("unused_internal")
            if not include_zombie_packages:
                _excluded_kinds.add("zombie_package")

            repo_filtered = repo_findings
            if kind:
                repo_filtered = [f for f in repo_filtered if f.kind == kind]
            elif _excluded_kinds:
                repo_filtered = [f for f in repo_filtered if f.kind not in _excluded_kinds]
            if safe_only:
                repo_filtered = [f for f in repo_filtered if f.safe_to_delete]
            if min_confidence > 0:
                repo_filtered = [f for f in repo_filtered if f.confidence >= min_confidence]
            if directory:
                prefix = directory.rstrip("/") + "/"
                repo_filtered = [f for f in repo_filtered if f.file_path.startswith(prefix)]
            if owner:
                owner_lower = owner.lower()
                repo_filtered = [
                    f
                    for f in repo_filtered
                    if f.primary_owner and f.primary_owner.lower() == owner_lower
                ]

            for f in repo_filtered:
                serialized = _serialize_finding(f, git_meta_map)
                serialized["repo"] = ctx.alias
                merged_findings.append(serialized)

            # Accumulate summary stats from unfiltered findings
            total_all += len(repo_findings)
            total_deletable += sum(f.lines for f in repo_findings if f.safe_to_delete)
            total_safe += sum(1 for f in repo_findings if f.safe_to_delete)
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

        # Build pseudo-tiered structure from serialized dicts
        high = [f for f in merged_findings if f["confidence"] >= 0.8]
        medium = [f for f in merged_findings if 0.5 <= f["confidence"] < 0.8]
        low = [f for f in merged_findings if f["confidence"] < 0.5]

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
            tiers["high"] = _tier_from_dicts(
                high,
                "High confidence (>=0.8): Zero references in the codebase. Safe to delete.",
            )
        if tier is None or tier == "medium":
            tiers["medium"] = _tier_from_dicts(
                medium,
                "Medium confidence (0.5-0.8): Likely unused but may have indirect references. Review before deleting.",
            )
        if tier is None or tier == "low":
            tiers["low"] = _tier_from_dicts(
                low,
                "Low confidence (<0.5): Potentially used via dynamic imports or reflection. Investigate first.",
            )

        return {
            "workspace": True,
            "summary": summary,
            "tiers": tiers,
            "impact": _compute_impact(tiers),
        }

    # --- Single repo path ---
    ctx = await _resolve_repo_context(repo)
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
        finding_paths = list({f.file_path for f in all_findings})
        git_meta_map: dict[str, Any] = {}
        if finding_paths:
            git_res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repository.id,
                    GitMetadata.file_path.in_(finding_paths),
                )
            )
            git_meta_map = {g.file_path: g for g in git_res.scalars().all()}

    # --- Build excluded kinds from scope flags ---
    _excluded_kinds: set[str] = set()
    if no_unreachable:
        _excluded_kinds.add("unreachable_file")
    if no_unused_exports:
        _excluded_kinds.add("unused_export")
    if not include_internals:
        _excluded_kinds.add("unused_internal")
    if not include_zombie_packages:
        _excluded_kinds.add("zombie_package")

    # --- Apply filters ---
    filtered = all_findings
    if kind:
        filtered = [f for f in filtered if f.kind == kind]
    elif _excluded_kinds:
        filtered = [f for f in filtered if f.kind not in _excluded_kinds]
    if safe_only:
        filtered = [f for f in filtered if f.safe_to_delete]
    if min_confidence > 0:
        filtered = [f for f in filtered if f.confidence >= min_confidence]
    if directory:
        prefix = directory.rstrip("/") + "/"
        filtered = [f for f in filtered if f.file_path.startswith(prefix)]
    if owner:
        owner_lower = owner.lower()
        filtered = [
            f for f in filtered if f.primary_owner and f.primary_owner.lower() == owner_lower
        ]

    # --- Build tiered structure ---
    tiers = _build_tiers(filtered, limit, tier, git_meta_map)

    # --- Summary across ALL open findings (unfiltered) ---
    by_kind: dict[str, int] = {}
    for f in all_findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    summary = {
        "total_findings": len(all_findings),
        "filtered_findings": len(filtered),
        "deletable_lines": sum(f.lines for f in all_findings if f.safe_to_delete),
        "safe_to_delete_count": sum(1 for f in all_findings if f.safe_to_delete),
        "by_kind": by_kind,
    }

    result: dict[str, Any] = {"summary": summary, "tiers": tiers}

    # --- Grouping views ---
    if group_by == "directory":
        result["by_directory"] = _rollup_by_directory(filtered)
    elif group_by == "owner":
        result["by_owner"] = _rollup_by_owner(filtered)

    # --- Impact estimate ---
    result["impact"] = _compute_impact(tiers)

    return result


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


def _serialize_finding(f: Any, git_meta_map: dict | None = None) -> dict:
    """Serialize a single DeadCodeFinding to dict."""
    result = {
        "kind": f.kind,
        "file_path": f.file_path,
        "symbol_name": f.symbol_name,
        "confidence": f.confidence,
        "reason": f.reason,
        "safe_to_delete": f.safe_to_delete,
        "lines": f.lines,
        "last_commit_at": f.last_commit_at.isoformat() if f.last_commit_at else None,
        "primary_owner": f.primary_owner,
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
) -> dict:
    """Split findings into high/medium/low confidence tiers."""
    high = sorted(
        [f for f in findings if f.confidence >= 0.8],
        key=lambda f: (-f.confidence, -f.lines),
    )
    medium = sorted(
        [f for f in findings if 0.5 <= f.confidence < 0.8],
        key=lambda f: (-f.confidence, -f.lines),
    )
    low = sorted(
        [f for f in findings if f.confidence < 0.5],
        key=lambda f: (-f.confidence, -f.lines),
    )

    def _tier_block(name: str, items: list, description: str) -> dict:
        return {
            "description": description,
            "count": len(items),
            "lines": sum(f.lines for f in items),
            "safe_count": sum(1 for f in items if f.safe_to_delete),
            "findings": [_serialize_finding(f, git_meta_map) for f in items[:limit]],
            "truncated": len(items) > limit,
        }

    tiers = {}
    if tier_filter is None or tier_filter == "high":
        tiers["high"] = _tier_block(
            "high",
            high,
            "High confidence (>=0.8): Zero references in the codebase. Safe to delete.",
        )
    if tier_filter is None or tier_filter == "medium":
        tiers["medium"] = _tier_block(
            "medium",
            medium,
            "Medium confidence (0.5-0.8): Likely unused but may have indirect references. Review before deleting.",
        )
    if tier_filter is None or tier_filter == "low":
        tiers["low"] = _tier_block(
            "low",
            low,
            "Low confidence (<0.5): Potentially used via dynamic imports or reflection. Investigate first.",
        )
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
        if f.safe_to_delete:
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
        if f.safe_to_delete:
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
            "Start with the 'high' tier — these have zero references and are safe to remove. "
            "Then review 'medium' tier findings with your team."
            if total_lines > 0
            else "No dead code found matching your filters."
        ),
    }

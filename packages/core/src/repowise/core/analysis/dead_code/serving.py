"""Serving stored dead-code findings: filter, tier, roll up, serialize.

The single-repo and workspace answers share every step here, so a finding
reads the same whichever path served it. Rows in (ORM rows, dataclasses or
mappings, read through :func:`field`), plain dicts out; no session.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Protocol

from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
    effective_safe_to_delete,
    path_risk_factors,
)
from repowise.core.analysis.finding_registry import verification_label, withheld_summary
from repowise.core.analysis.health.rows import field
from repowise.core.references import path_identity, stable_entity_id

# The bands findings are tiered by, in one place: the tier descriptions quote
# them and ``min_confidence="high"`` resolves to them, so the vocabulary the
# response is organised by and the one it accepts cannot drift apart (#1496).
# The engine's own thresholds, so the MCP tool, the web and the CLI
# (``DEAD_CODE_CONFIDENCE`` in packages/types/src/dead-code.ts) put a finding in
# the same tier.
TIER_FLOORS: dict[str, float] = {
    "high": SAFE_CONFIDENCE_THRESHOLD,
    "medium": RISK_CAP_CONFIDENCE,
    "low": 0.0,
}

TIER_DESCRIPTIONS: dict[str, str] = {
    "high": (
        f"High confidence (>={SAFE_CONFIDENCE_THRESHOLD}): No references found in the codebase. "
        "Strong cleanup candidates — review (especially whole files and runtime-loaded files) "
        "before deleting."
    ),
    "medium": (
        f"Medium confidence ({RISK_CAP_CONFIDENCE}-{SAFE_CONFIDENCE_THRESHOLD}): Likely unused "
        "but may have indirect references. Review before deleting."
    ),
    "low": (
        f"Low confidence (<{RISK_CAP_CONFIDENCE}): Potentially used via dynamic imports or "
        "reflection. Investigate first."
    ),
}

SUMMARY_SCOPE = (
    "total_findings, by_kind, deletable_lines and safe_to_delete_count cover every "
    "open finding; filtered_findings and tiers cover what the filters kept "
    f"(min_confidence defaults to {RISK_CAP_CONFIDENCE})"
)


def excluded_kinds(
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


# These literals must match get_dead_code()'s default parameters;
# test_get_dead_code_summary_says_what_it_counts catches drift between them.
DEFAULT_EXCLUDED_KINDS = excluded_kinds(
    no_unreachable=False,
    no_unused_exports=False,
    include_internals=False,
    include_zombie_packages=True,
)


@dataclass
class FindingFilters:
    """Filter parameters shared by the single-repo and workspace paths."""

    kind: str | None
    safe_only: bool
    min_confidence: float
    directory: str | None
    owner: str | None
    excluded_kinds: set[str] = dataclass_field(default_factory=set)
    # Kinds the finding-type registry keeps off this surface. Applied before
    # anything is counted, so no total describes a finding the caller cannot see.
    withheld_kinds: frozenset[str] = frozenset()

    def split_withheld(self, findings: list) -> tuple[list, dict[str, dict]]:
        """``(shown rows, {withheld kind: count/status/reason})``."""
        counts: dict[str, int] = {}
        for f in findings:
            kind = field(f, "kind")
            counts[kind] = counts.get(kind, 0) + 1
        shown = [f for f in findings if field(f, "kind") not in self.withheld_kinds]
        return shown, withheld_summary(counts, self.withheld_kinds)

    def applied(self) -> dict[str, Any]:
        """The filters that differ from the defaults, as the summary echoes them."""
        applied: dict[str, Any] = {}
        if self.kind:
            applied["kind"] = self.kind
        elif self.excluded_kinds != DEFAULT_EXCLUDED_KINDS:
            applied["excluded_kinds"] = sorted(self.excluded_kinds)
        if self.safe_only:
            applied["safe_only"] = True
        if self.min_confidence != RISK_CAP_CONFIDENCE:
            applied["min_confidence"] = self.min_confidence
        if self.directory:
            applied["directory"] = self.directory
        if self.owner:
            applied["owner"] = self.owner
        return applied

    def summarize(self, summary: dict[str, Any]) -> None:
        """Say which counts the filters touched, so empty tiers beside a
        non-zero ``total_findings`` do not read as a contradiction."""
        summary["scope"] = SUMMARY_SCOPE
        applied = self.applied()
        if applied:
            summary["filters"] = applied


def _predicates(filters: FindingFilters) -> list[Callable[[Any], bool]]:
    checks: list[Callable[[Any], bool]] = []
    if filters.kind:
        checks.append(lambda f: field(f, "kind") == filters.kind)
    elif filters.excluded_kinds:
        checks.append(lambda f: field(f, "kind") not in filters.excluded_kinds)
    if filters.safe_only:
        checks.append(is_safe)
    if filters.min_confidence > 0:
        checks.append(lambda f: field(f, "confidence") >= filters.min_confidence)
    if filters.directory:
        prefix = filters.directory.rstrip("/") + "/"
        checks.append(lambda f: field(f, "file_path").startswith(prefix))
    if filters.owner:
        owner = filters.owner.lower()
        checks.append(lambda f: (field(f, "primary_owner") or "").lower() == owner)
    return checks


def apply_filters(findings: list, filters: FindingFilters) -> list:
    """Keep the findings that pass the kind/safety/confidence/directory/owner filters."""
    checks = _predicates(filters)
    return [f for f in findings if all(check(f) for check in checks)]


def build_summary(
    shown: list,
    filtered_count: int,
    filters: FindingFilters,
    withheld: dict[str, dict],
) -> dict[str, Any]:
    """Counts over every shown open finding, plus what the filters kept."""
    by_kind: dict[str, int] = {}
    for f in shown:
        kind = field(f, "kind")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    safe = [f for f in shown if is_safe(f)]
    summary: dict[str, Any] = {
        "total_findings": len(shown),
        "filtered_findings": filtered_count,
        "deletable_lines": sum(field(f, "lines") or 0 for f in safe),
        "safe_to_delete_count": len(safe),
        "by_kind": by_kind,
    }
    filters.summarize(summary)
    if withheld:
        summary["withheld_types"] = withheld
    return summary


def is_safe(f: Any) -> bool:
    """Re-derive deletion-readiness for a stored finding.

    Mirrors the API/CLI: the persisted boolean is only ever downgraded, never
    trusted blindly, so config/bootstrap/database/environment/script/asset
    files (and findings written before risk factors existed) never read as
    safe-to-delete.
    """
    return effective_safe_to_delete(
        field(f, "confidence"),
        field(f, "file_path"),
        field(f, "safe_to_delete"),
        field(f, "kind"),
    )


def dead_code_finding_id(f: Any, repository: str) -> str:
    """The stable public id of one dead-code finding."""
    return stable_entity_id(
        "finding",
        repository,
        {
            "family": "dead_code",
            "path": path_identity(field(f, "file_path")),
            "kind": field(f, "kind"),
            "symbol": field(f, "symbol_name") or "",
            "line_start": field(f, "start_line"),
            "line_end": field(f, "end_line"),
        },
    )


def last_meaningful_change(gm: Any) -> str | None:
    """Date of the most recent significant (feature/fix) commit, if any."""
    if gm is None:
        return None
    # Significant commits already filter style/chore noise, most recent first.
    sig_json = field(gm, "significant_commits_json")
    if not sig_json:
        return None
    try:
        commits = json.loads(sig_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return commits[0].get("date") if commits else None


def serialize_finding(
    f: Any,
    git_meta_map: dict | None = None,
    *,
    repository: str = "default",
) -> dict:
    """Serialize one stored finding to the shape every surface emits."""
    last_commit_at = field(f, "last_commit_at")
    result = {
        "id": dead_code_finding_id(f, repository),
        "repository": repository,
        "kind": field(f, "kind"),
        "file_path": field(f, "file_path"),
        "symbol_name": field(f, "symbol_name"),
        "symbol_kind": field(f, "symbol_kind"),
        "start_line": field(f, "start_line"),
        "end_line": field(f, "end_line"),
        "confidence": field(f, "confidence"),
        "reason": field(f, "reason"),
        "safe_to_delete": is_safe(f),
        "risk_factors": list(path_risk_factors(field(f, "file_path"))),
        "lines": field(f, "lines"),
        "last_commit_at": last_commit_at.isoformat() if last_commit_at else None,
        # Recent churn is the top rung of the confidence ladder, so the agent
        # gets the same reason for a low score that the web page shows.
        "commit_count_90d": field(f, "commit_count_90d"),
        "primary_owner": field(f, "primary_owner"),
        # NB: age_days runs from the file's *first* commit, so it is the file's
        # age, not how long this has been dead — the two disagree on 75% of
        # findings. last_commit_at above is the staleness signal.
        "age_days": field(f, "age_days"),
    }
    label = verification_label(result["kind"])
    if label:
        result["verification"] = label
    if git_meta_map:
        meaningful = last_meaningful_change(git_meta_map.get(result["file_path"]))
        if meaningful:
            result["last_meaningful_change"] = meaningful
    return result


def _tier_of(confidence: float) -> str:
    if confidence >= TIER_FLOORS["high"]:
        return "high"
    if confidence >= TIER_FLOORS["medium"]:
        return "medium"
    return "low"


def build_tiers(
    findings: list[dict],
    limit: int,
    tier: str | None,
    *,
    on_overflow: Callable[[str, list[dict]], None] | None = None,
) -> dict[str, Any]:
    """Split serialized findings into high/medium/low tiers, best first.

    *on_overflow* receives ``(tier name, findings beyond limit)`` so a caller
    can keep what the per-tier limit cut instead of dropping it silently.
    """
    banded: dict[str, list[dict]] = {"high": [], "medium": [], "low": []}
    for f in findings:
        banded[_tier_of(f["confidence"])].append(f)

    tiers: dict[str, Any] = {}
    for name, items in banded.items():
        if tier is not None and tier != name:
            continue
        items.sort(key=lambda f: (-f["confidence"], -(f["lines"] or 0)))
        if on_overflow is not None and len(items) > limit:
            on_overflow(name, items[limit:])
        tiers[name] = _tier_block(name, items, limit)
    return tiers


def _tier_block(name: str, items: list[dict], limit: int) -> dict[str, Any]:
    return {
        "description": TIER_DESCRIPTIONS[name],
        "count": len(items),
        "lines": sum(f["lines"] or 0 for f in items),
        "safe_count": sum(1 for f in items if f["safe_to_delete"]),
        "findings": items[:limit],
        "truncated": len(items) > limit,
    }


def _rollup(findings: list, key: str, key_of: Callable[[Any], str]) -> list[dict]:
    groups: dict[str, dict] = {}
    for f in findings:
        name = key_of(f)
        group = groups.setdefault(name, {key: name, "count": 0, "lines": 0, "safe_count": 0})
        group["count"] += 1
        group["lines"] += field(f, "lines") or 0
        if is_safe(f):
            group["safe_count"] += 1
    return sorted(groups.values(), key=lambda g: -g["lines"])


def _directory_key(f: Any) -> str:
    # The first two path segments, or just the first for a shallow path.
    parts = field(f, "file_path").split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def rollup_by_directory(findings: list) -> list[dict]:
    """Group findings by top-level directory, most lines first."""
    return _rollup(findings, "directory", _directory_key)


def rollup_by_owner(findings: list) -> list[dict]:
    """Group findings by primary owner, most lines first."""
    return _rollup(findings, "owner", lambda f: field(f, "primary_owner") or "unowned")


def compute_impact(tiers: dict) -> dict:
    """Total impact across the tiers served."""
    total_lines = sum(t["lines"] for t in tiers.values())
    # Approximate: only the findings a tier shows count toward safe lines.
    safe_lines = sum(
        f["lines"] or 0 for t in tiers.values() for f in t["findings"] if f["safe_to_delete"]
    )
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


class CrossRepoLookup(Protocol):
    """What :func:`adjust_cross_repo` needs from the workspace's cross-repo data."""

    def has_cross_repo_consumers(self, repo_alias: str, file_path: str) -> list[dict]: ...

    def get_repos_depending_on(self, repo_alias: str) -> list[str]: ...


def adjust_cross_repo(
    tiers: dict, lookup: CrossRepoLookup | None, repo_alias: str | None = None
) -> None:
    """Lower confidence on served findings that other repos consume, in place.

    A finding's own ``repo`` wins over *repo_alias*, so one call covers both a
    single repo and the workspace-wide merge.
    """
    if lookup is None:
        return
    for tier_data in tiers.values():
        for finding in tier_data.get("findings", []):
            alias = finding.get("repo", repo_alias)
            if alias:
                _adjust_one(finding, lookup, alias)


def _adjust_one(finding: dict, lookup: CrossRepoLookup, alias: str) -> None:
    consumers = lookup.has_cross_repo_consumers(alias, finding.get("file_path", ""))
    if consumers:
        finding["confidence"] = round(finding["confidence"] * 0.5, 2)
        consumer_repos = sorted({c["repo"] for c in consumers})
        finding["cross_repo_note"] = (
            f"Confidence reduced: {len(consumers)} cross-repo consumer(s) "
            f"in {', '.join(consumer_repos)}."
        )
        return
    # Package-level dependents may consume an export without a file edge.
    if finding.get("kind") != "unused_export":
        return
    depending = lookup.get_repos_depending_on(alias)
    if depending:
        finding["confidence"] = round(finding["confidence"] * 0.3, 2)
        finding["cross_repo_note"] = (
            f"This export may be consumed by: {', '.join(depending)}. Verify before deletion."
        )

"""Serving stored dead-code findings: filter, tier, roll up, serialize.

The single-repo and workspace answers share every step here, so a finding
reads the same whichever path served it. Rows in (ORM rows, dataclasses or
mappings, read through :func:`field`), plain dicts out; no session.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Protocol

from repowise.core.analysis.dead_code.deletion_gate import effective_safe_to_delete
from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
    path_risk_factors,
)
from repowise.core.analysis.finding_registry import (
    split_gated,
    verification_label,
    withheld_summary,
)
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
    # Shows the findings a language gate holds back (``LANGUAGE_GATES``).
    include_unverified: bool = False

    def split_gated(self, findings: list) -> tuple[list, dict[str, dict]]:
        """``(shown rows, {gated language: count/reason})``."""
        return split_gated(
            "dead_code",
            findings,
            kind="kind",
            get=field,
            include_unverified=self.include_unverified,
        )

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


def summary_counts(shown: list) -> dict[str, Any]:
    """Counts over every shown open finding; merge per-repo parts with
    :func:`merge_summary_counts` so no caller holds every repo's rows at once.
    Pass :func:`serve_findings` rows so safe counts agree with the tiers.
    """
    by_kind: dict[str, int] = {}
    for f in shown:
        kind = field(f, "kind")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    safe = [f for f in shown if is_safe(f)]
    return {
        "total_findings": len(shown),
        "deletable_lines": sum(field(f, "lines") or 0 for f in safe),
        "safe_to_delete_count": len(safe),
        "by_kind": by_kind,
    }


def merge_summary_counts(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum :func:`summary_counts` parts; ``by_kind`` keeps first-seen order."""
    merged = summary_counts([])
    for part in parts:
        for key in ("total_findings", "deletable_lines", "safe_to_delete_count"):
            merged[key] += part[key]
        for kind, count in part["by_kind"].items():
            merged["by_kind"][kind] = merged["by_kind"].get(kind, 0) + count
    return merged


def safe_only_note(total: int, safe: int) -> str | None:
    """Why a safe-only view is empty when findings exist but none is
    deletion-ready; None otherwise. The MCP summary and the CLI share it."""
    if safe or not total:
        return None
    return (
        f"{total} finding(s), none deletion-ready: a finding is deletion-ready only "
        "when missing imports are evidence for its language. Review the ranked list "
        "without the safe-only filter."
    )


def build_summary(
    counts: dict[str, Any],
    filtered_count: int,
    filters: FindingFilters,
    withheld: dict[str, dict],
    gated: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """The summary block: *counts* from :func:`summary_counts`, plus what the
    filters kept and what the registry held back."""
    summary: dict[str, Any] = {
        "total_findings": counts["total_findings"],
        "filtered_findings": filtered_count,
        "deletable_lines": counts["deletable_lines"],
        "safe_to_delete_count": counts["safe_to_delete_count"],
        "by_kind": counts["by_kind"],
    }
    filters.summarize(summary)
    if filters.safe_only and (
        note := safe_only_note(counts["total_findings"], counts["safe_to_delete_count"])
    ):
        summary["safe_only_note"] = note
    if withheld:
        summary["withheld_types"] = withheld
    if gated:
        summary["gated"] = {**gated, "opt_in": {"include_unverified": True}}
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
    note = field(f, "cross_repo_note")
    if note:
        result["cross_repo_note"] = note
    if git_meta_map:
        meaningful = last_meaningful_change(git_meta_map.get(result["file_path"]))
        if meaningful:
            result["last_meaningful_change"] = meaningful
    return result


def tier_of(confidence: float) -> str:
    """The tier *confidence* falls in, by :data:`TIER_FLOORS`."""
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
        banded[tier_of(f["confidence"])].append(f)

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
        "recommendation": _recommendation(tiers),
    }


def _recommendation(tiers: dict) -> str:
    """Point at the best tier that has findings, never at an empty one."""
    if tiers.get("high", {}).get("count"):
        return (
            "Start with the 'high' tier — these have no references in the graph and are the "
            "strongest cleanup candidates. Confirm each (runtime-loaded config/bootstrap/database "
            "files are flagged but never auto-marked safe), then review 'medium' tier with your team."
        )
    if tiers.get("medium", {}).get("count"):
        return (
            "Review the 'medium' tier with your team — likely unused but may have indirect "
            "references. Confirm each before deleting."
        )
    if tiers.get("low", {}).get("count"):
        return (
            "Only 'low' tier findings — they may be used via dynamic imports or reflection. "
            "Investigate before deleting."
        )
    return "No dead code found matching your filters."


class CrossRepoLookup(Protocol):
    """What :func:`serve_findings` needs from the workspace's cross-repo data."""

    def has_cross_repo_consumers(self, repo_alias: str, file_path: str) -> list[dict]: ...

    def get_repos_depending_on(self, repo_alias: str) -> list[str]: ...

    def get_package_consumers(self, repo_alias: str) -> list[dict]: ...

    def get_contract_links_as_provider(self, repo_alias: str, file_path: str) -> list[dict]: ...


@dataclass(frozen=True)
class CrossRepoCut:
    """What another repo's use does to a finding: never safe, maybe lower, and why."""

    factor: float
    note: str


# A contract link names the file another repo calls; co-change and package
# links only say a consumer may exist, so they withdraw safety and leave the
# confidence alone. Measured on a three-repo workspace: 7 of 7 findings those
# weaker links had cut had no consumer in another repo.
_CONTRACT_FACTOR = 0.3


def cross_repo_cut(
    lookup: CrossRepoLookup | None, alias: str | None, file_path: str, kind: Any
) -> CrossRepoCut | None:
    """What another workspace repo's use of *file_path* does to its finding, or None.

    A contract link (HTTP route, generated client) from another repo lowers
    confidence for every kind. Co-change partners in another repo, or a
    package dependent of an export's repo, only withdraw ``safe_to_delete``.
    """
    if lookup is None or not alias:
        return None
    if contract_repos := _contract_consumers(lookup, alias, file_path):
        noun = "export" if kind == "unused_export" else "file"
        return CrossRepoCut(
            _CONTRACT_FACTOR,
            f"This {noun} may be consumed by: {', '.join(sorted(contract_repos))}. "
            "Verify before deletion.",
        )
    if partners := lookup.has_cross_repo_consumers(alias, file_path):
        repos = sorted({c["repo"] for c in partners})
        return CrossRepoCut(
            1.0,
            f"Not cleanup-ready: changes together with {len(partners)} file(s) in "
            f"{', '.join(repos)}. Verify before deletion.",
        )
    if kind == "unused_export" and lookup.get_repos_depending_on(alias):
        return CrossRepoCut(1.0, _package_dependent_note(lookup, alias, file_path))
    return None


class ServedFinding:
    """A stored finding as every surface shows it: the cross-repo cut applied.

    Reads through to the row for every other field, so filters, serializers
    and response models take it wherever they take the row.
    """

    __slots__ = ("confidence", "cross_repo_note", "row", "safe_to_delete")

    def __init__(self, row: Any, cut: CrossRepoCut | None) -> None:
        self.row = row
        self.confidence = field(row, "confidence")
        self.safe_to_delete = field(row, "safe_to_delete")
        self.cross_repo_note = None
        if cut is not None:
            if cut.factor != 1.0:
                self.confidence = round(self.confidence * cut.factor, 2)
            self.safe_to_delete = False
            self.cross_repo_note = cut.note

    def __getattr__(self, name: str) -> Any:
        row = object.__getattribute__(self, "row")
        if isinstance(row, Mapping):
            try:
                return row[name]
            except KeyError:
                raise AttributeError(name) from None
        return getattr(row, name)


def serve_findings(
    rows: list,
    lookup: CrossRepoLookup | None,
    alias: str | None,
    filters: FindingFilters | None = None,
) -> list[ServedFinding]:
    """The one read every surface serves findings through.

    The cross-repo cut runs first, so the confidence floor, ``safe_only`` and
    the tiers all see the confidence the finding is shown with (#4196).
    """
    served = [
        ServedFinding(r, cross_repo_cut(lookup, alias, field(r, "file_path"), field(r, "kind")))
        for r in rows
    ]
    return apply_filters(served, filters) if filters is not None else served


def _contract_consumers(lookup: CrossRepoLookup, alias: str, file_path: str) -> set[str]:
    """The other repos a recorded contract link from *file_path* reaches."""
    return {
        link["consumer_repo"]
        for link in lookup.get_contract_links_as_provider(alias, file_path)
        if link.get("consumer_repo") not in (None, alias)
    }


def _package_dependent_note(lookup: CrossRepoLookup, alias: str, file_path: str) -> str:
    """Name only the dependents whose package link reaches *file_path*.

    Safety is withdrawn either way: package links miss consumers (an
    unresolved PyPI pin, a CLI call), so they can name a consumer but never
    prove there is none.
    """
    reaching = {
        link["source_repo"]
        for link in lookup.get_package_consumers(alias)
        if _inside_manifest(file_path, link.get("target_manifest"))
    }
    if reaching:
        return (
            f"Not cleanup-ready: {', '.join(sorted(reaching))} may consume this export. "
            "Verify before deletion."
        )
    return (
        "Not cleanup-ready: another workspace repo depends on this one, though no recorded "
        "package or contract link reaches this file. Verify before deletion."
    )


def _inside_manifest(file_path: str, manifest: str | None) -> bool:
    """Whether *file_path* sits in the package *manifest* declares; unknown counts as inside."""
    if not manifest:
        return True
    root = posixpath.dirname(manifest)
    return not root or file_path.startswith(root + "/")

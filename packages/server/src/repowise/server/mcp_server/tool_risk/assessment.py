"""Single-target risk scoring for get_risk."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    GitMetadata,
    GraphNode,
    Repository,
)
from repowise.server.mcp_server._helpers import (
    filter_dicts_by_key,
)

#: A file carrying this many counted bug fixes reads as bug-prone. Same trigger
#: the PR bot uses for prior defects, so the two surfaces agree on "a lot".
_BUG_PRONE_FIXES = 3

#: How many attributed symbols the defect profile names. Enough to say "mostly
#: these", short enough to keep the block inside the per-file token budget.
_TOP_FIX_SYMBOLS = 3


def _derive_change_pattern(categories: dict[str, int]) -> str:
    """Derive a human-readable change pattern from commit category counts."""
    if not categories:
        return "uncategorized"
    total = sum(categories.values())
    if total == 0:
        return "uncategorized"
    dominant = max(categories, key=lambda k: categories[k])
    ratio = categories[dominant] / total
    if ratio >= 0.5:
        labels = {
            "feature": "feature-active",
            "refactor": "primarily refactored",
            "fix": "fix-heavy",
            "dependency": "dependency-churn",
        }
        return labels.get(dominant, dominant)
    return "mixed-activity"


def _compute_trend(meta: Any) -> str:
    """Compute risk velocity from 30d vs 90d commit rates."""
    c30 = meta.commit_count_30d or 0
    c90 = meta.commit_count_90d or 0
    # Baseline: commits in the 60-day window before the last 30 days
    baseline_commits = c90 - c30
    recent_rate = c30 / 30.0
    baseline_rate = baseline_commits / 60.0

    if c90 == 0:
        return "stable"
    if baseline_rate == 0:
        return "increasing" if c30 > 0 else "stable"
    ratio = recent_rate / baseline_rate
    if ratio > 1.5:
        return "increasing"
    elif ratio < 0.5:
        return "decreasing"
    return "stable"


def _classify_risk_type(meta: Any, dep_count: int, team_size: int | None = None) -> str:
    """Classify risk as churn-heavy, bug-prone, high-coupling, or bus-factor-risk.

    *team_size* is the repo's active-contributor count (90d). On a small
    team (≤ SMALL_TEAM_MAX_CONTRIBUTORS) a single-author file is the
    expected operating model, so ``bus-factor-risk`` is reserved for
    hotspot-active files there (issue #361). ``None`` = unknown → keep
    the historical behaviour.

    ``bug-prone`` reads the counted fix history rather than the old keyword
    scan over ``significant_commits``: that scan matched "fix" anywhere in a
    subject, counted doc and test commits, and had no recency at all, while
    ``prior_defect_count`` is the shape-filtered windowed count and
    ``bug_magnet`` is its decayed form. An index that predates the fix-event
    rollup reports 0/None here and simply falls through to the next rule,
    the same way every other backfilled column behaves until the next update.
    """
    from repowise.core.analysis.health.biomarkers.base import SMALL_TEAM_MAX_CONTRIBUTORS

    churn_score = meta.churn_percentile or 0.0
    bus_factor = getattr(meta, "bus_factor", 0) or 0
    total_commits = meta.commit_count_total or 0

    small_team = team_size is not None and team_size <= SMALL_TEAM_MAX_CONTRIBUTORS

    # Bug-prone takes priority: real counted fixes, decayed or plain.
    prior_defects = getattr(meta, "prior_defect_count", 0) or 0
    if getattr(meta, "bug_magnet", False) or prior_defects >= _BUG_PRONE_FIXES:
        return "bug-prone"
    if churn_score >= 0.7:
        return "churn-heavy"
    if (
        bus_factor == 1
        and total_commits > 20
        and (not small_team or bool(getattr(meta, "is_hotspot", False)))
    ):
        return "bus-factor-risk"
    if dep_count >= 5:
        return "high-coupling"
    return "stable"


async def _get_active_contributor_count(session: AsyncSession, repo_id: str) -> int | None:
    """Repo-wide active-contributor count from persisted git metadata.

    Reuses ``count_active_contributors`` (per-author ``last_commit_ts`` in
    ``top_authors_json``) over all rows. ``None`` = unknown (no rows, or an
    index that predates per-author timestamps).
    """
    from repowise.core.ingestion.git_indexer import count_active_contributors

    try:
        rows = await session.execute(
            select(GitMetadata.top_authors_json).where(GitMetadata.repository_id == repo_id)
        )
        metas = [{"top_authors_json": r[0]} for r in rows.all() if r[0]]
        if not metas:
            return None
        return count_active_contributors(metas)
    except Exception:
        return None


def _compute_impact_surface(
    target: str,
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
    exclude_spec: Any = None,
) -> list[dict]:
    """Find the top 3 most critical modules that depend on this file."""
    # BFS up to 2 hops through reverse dependencies
    visited: set[str] = set()
    frontier = {target}
    for _ in range(2):
        next_frontier: set[str] = set()
        for node in frontier:
            for dep in reverse_deps.get(node, set()):
                if dep != target and dep not in visited:
                    visited.add(dep)
                    next_frontier.add(dep)
        frontier = next_frontier

    if not visited:
        return []

    # Rank by pagerank (most critical first)
    ranked = []
    for dep in visited:
        meta = node_meta.get(dep)
        ranked.append(
            {
                "file_path": dep,
                "pagerank": meta.pagerank if meta else 0.0,
                "is_entry_point": meta.is_entry_point if meta else False,
            }
        )
    ranked.sort(key=lambda x: -x["pagerank"])
    ranked = filter_dicts_by_key(ranked, "file_path", exclude_spec)
    return ranked[:3]


async def _check_test_gap(session: AsyncSession, repo_id: str, target: str) -> bool:
    """Return True if *target* has no test, coverage-backed where the map has data.

    A file with a per-test coverage row (from ``repowise coverage add``) is
    coverage-*proven* tested, so it is never a gap. Where the map has no data for
    the file, fall back to the filename pattern (test_<name>, <name>_test,
    <name>.spec.*) - an honest "unknown", never asserted as untested. Test files
    themselves (is_test=True) are never a gap.
    """
    import os

    from repowise.core.persistence.crud import covered_source_files

    # Test files don't need tests — skip the check entirely
    node_res = await session.execute(
        select(GraphNode.is_test)
        .where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id == target,
        )
        .limit(1)
    )
    row = node_res.scalar_one_or_none()
    if row is True:
        return False

    # Coverage proves a test exercises this file: not a gap.
    if await covered_source_files(session, repo_id, {target}):
        return False

    base = os.path.splitext(os.path.basename(target))[0]
    ext = os.path.splitext(target)[1].lstrip(".")
    # Build a LIKE pattern broad enough to catch test_<base>, <base>_test, <base>.spec.*
    patterns = [f"%test_{base}%", f"%{base}_test%", f"%{base}.spec.{ext}%"]
    for pat in patterns:
        res = await session.execute(
            select(GraphNode)
            .where(
                GraphNode.repository_id == repo_id,
                GraphNode.is_test == True,  # noqa: E712
                GraphNode.node_id.like(pat),
            )
            .limit(1)
        )
        if res.scalar_one_or_none() is not None:
            return False
    return True


async def _get_security_signals(session: AsyncSession, repo_id: str, target: str) -> list[dict]:
    """Fetch stored security findings for *target* from security_findings table."""
    try:
        rows = await session.execute(
            text(
                "SELECT kind, severity, snippet FROM security_findings "
                "WHERE repository_id = :repo_id AND file_path = :fp "
                "ORDER BY severity DESC, kind"
            ),
            {"repo_id": repo_id, "fp": target},
        )
        return [{"kind": r[0], "severity": r[1], "snippet": r[2]} for r in rows.all()]
    except Exception:
        return []


def _build_co_changes(meta: Any, import_related: set[str], exclude_spec: Any) -> list[dict]:
    """Top-5 co-change partners for *meta*, by frequency, with import-link flags.

    Larger lists make MCP responses verbose without adding signal: top-5 captures
    the bulk of the temporal-coupling mass and keeps tool output tight for agents.
    """
    partners = json.loads(meta.co_change_partners_json)
    partners_sorted = sorted(
        partners,
        key=lambda p: p.get("co_change_count", p.get("count", 0)) or 0,
        reverse=True,
    )[:5]
    return filter_dicts_by_key(
        [
            {
                "file_path": p.get("file_path", p.get("path", "")),
                "count": p.get("co_change_count", p.get("count", 0)),
                "last_co_change": p.get("last_co_change"),
                "has_import_link": p.get("file_path", p.get("path", "")) in import_related,
            }
            for p in partners_sorted
        ],
        "file_path",
        exclude_spec,
    )


def fix_annotation(meta: Any) -> dict | None:
    """Counted fixes, their age, and the magnet flag, or ``None`` for silence.

    The compact form every fix-history surface shares, so the recency contract
    is enforced once: the ``bug_magnet`` flag rides on the age and is never
    emitted alone. ``bug_magnet`` is a claim about RECENT fix pressure, so with
    no timestamp to anchor it the same word would describe a file fixed four
    times last month and one fixed four times two years ago.

    Read off the ``GitMetadata`` row the caller already loaded: no query.
    """
    count = getattr(meta, "prior_defect_count", 0) or 0
    if count <= 0:
        return None

    out: dict[str, Any] = {"fix_count": count}
    last_fix_at = getattr(meta, "last_fix_at", None)
    if isinstance(last_fix_at, datetime):
        # Rows are stored naive-UTC; compare on the same footing.
        moment = last_fix_at if last_fix_at.tzinfo else last_fix_at.replace(tzinfo=UTC)
        out["last_fix_days_ago"] = max(0, (datetime.now(UTC) - moment).days)
        if getattr(meta, "bug_magnet", False):
            out["bug_magnet"] = True
    return out


def _fix_clause(profile: dict | None) -> str:
    """Lead clause for ``risk_summary``, or empty when there is no fix history.

    Trailing separator included so the caller concatenates without a dangling
    comma on files that have never been fixed. Never renders a count without an
    age: an unanchored count reads as a claim about the distant past.
    """
    if not profile or "last_fix_days_ago" not in profile:
        return ""
    n = profile["fix_count"]
    magnet = " (bug magnet)" if profile.get("bug_magnet") else ""
    return (
        f"{n} bug fix{'es' if n != 1 else ''} in 6mo, "
        f"last {profile['last_fix_days_ago']}d ago{magnet}, "
    )


def _defect_profile(meta: Any) -> dict | None:
    """What this file's counted bug fixes say about it, or ``None`` for silence.

    Built from the fix-event rollup already loaded on the ``GitMetadata`` row,
    so there is no second query. Silent when the file has no counted fixes, so a
    clean file and a repo with no fix history both add nothing to the response
    (the FileSignalsPanel convention).

    Every field is aggregate. No inducing commit is named here or anywhere else:
    file-level SZZ ran at 74.5% precision against the frozen judgments, which is
    fine for counting and too thin to accuse a commit.

    The approximation caveat on ``top_symbols`` lives in ``get_risk``'s docstring
    rather than in each row. It is the same sentence every time, and a constant
    string repeated once per target is exactly the per-file cost the lean-MCP
    work went to some trouble to remove.
    """
    profile = fix_annotation(meta)
    if profile is None:
        return None
    profile["window"] = "6 months"

    symbols = _top_fix_symbols(getattr(meta, "fix_symbol_counts_json", None))
    if symbols:
        profile["top_symbols"] = symbols
    return profile


def _top_fix_symbols(raw: str | None) -> dict[str, int]:
    """Top few ``symbol -> fix count`` pairs, with the redundant path stripped.

    Stored keys are ``path/to/file.py::Name`` and the caller already knows the
    path, so the prefix is pure token cost in an MCP response. The stored map is
    written in descending-count order, so "top few" is a slice.
    """
    if not raw:
        return {}
    try:
        counts = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(counts, dict):
        return {}
    return {str(k).rsplit("::", 1)[-1]: int(v) for k, v in list(counts.items())[:_TOP_FIX_SYMBOLS]}


def _load_commit_categories(meta: Any) -> dict:
    """Parse the persisted commit-category counts, tolerating malformed JSON."""
    categories: dict = {}
    cat_json = getattr(meta, "commit_categories_json", None)
    if cat_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            categories = json.loads(cat_json)
    return categories


async def _assess_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    all_edge_map: dict[str, int],
    import_links: dict[str, set[str]],
    reverse_deps: dict[str, set[str]],
    node_meta: dict[str, Any],
    exclude_spec: Any = None,
    team_size: int | None = None,
) -> dict:
    """Assess risk for a single target file.

    Enriches each result with:
    - test_gap: bool — True when no test file matching this file's basename exists.
    - security_signals: list of {kind, severity, snippet} from security_findings.
    """
    repo_id = repository.id
    result_data: dict[str, Any] = {"target": target}

    dep_count = all_edge_map.get(target, 0)

    # Git metadata
    res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.file_path == target,
        )
    )
    meta = res.scalar_one_or_none()

    if meta is None:
        result_data["hotspot_score"] = 0.0
        result_data["dependents_count"] = dep_count
        result_data["co_change_partners"] = []
        result_data["primary_owner"] = None
        result_data["owner_pct"] = None
        result_data["trend"] = "unknown"
        result_data["risk_type"] = "high-coupling" if dep_count >= 5 else "unknown"
        result_data["impact_surface"] = _compute_impact_surface(
            target,
            reverse_deps,
            node_meta,
            exclude_spec,
        )
        result_data["test_gap"] = await _check_test_gap(session, repo_id, target)
        result_data["security_signals"] = await _get_security_signals(session, repo_id, target)
        result_data["risk_summary"] = f"{target} — no git metadata available"
        return result_data

    hotspot_score = meta.churn_percentile or 0.0

    co_changes = _build_co_changes(meta, import_links.get(target, set()), exclude_spec)

    owner = meta.primary_owner_name or "unknown"
    pct = meta.primary_owner_commit_pct or 0.0

    # --- Risk velocity (trend) ---
    trend = _compute_trend(meta)

    # --- Risk type classification ---
    risk_type = _classify_risk_type(meta, dep_count, team_size)

    # --- Impact surface ---
    impact_surface = _compute_impact_surface(target, reverse_deps, node_meta, exclude_spec)

    # Phase 2: commit classification → change_pattern
    change_pattern = _derive_change_pattern(_load_commit_categories(meta))

    # Phase 2: recent owner & bus factor
    bus_factor = getattr(meta, "bus_factor", 0) or 0

    result_data["hotspot_score"] = hotspot_score
    result_data["dependents_count"] = dep_count
    result_data["co_change_partners"] = co_changes
    result_data["primary_owner"] = owner
    result_data["owner_pct"] = pct
    result_data["recent_owner"] = getattr(meta, "recent_owner_name", None)
    result_data["recent_owner_pct"] = getattr(meta, "recent_owner_commit_pct", None)
    result_data["bus_factor"] = bus_factor
    result_data["contributor_count"] = getattr(meta, "contributor_count", 0) or 0
    result_data["trend"] = trend
    result_data["risk_type"] = risk_type
    result_data["change_pattern"] = change_pattern
    result_data["change_magnitude"] = {
        "lines_added_90d": getattr(meta, "lines_added_90d", 0) or 0,
        "lines_deleted_90d": getattr(meta, "lines_deleted_90d", 0) or 0,
        "avg_commit_size": round(getattr(meta, "avg_commit_size", 0.0) or 0.0, 1),
    }
    result_data["impact_surface"] = impact_surface
    # Phase 3: rename tracking & merge commit proxy
    original_path = getattr(meta, "original_path", None)
    if original_path:
        result_data["original_path"] = original_path
    merge_commit_count = getattr(meta, "merge_commit_count_90d", 0) or 0
    if merge_commit_count > 0:
        result_data["merge_commit_count_90d"] = merge_commit_count

    defect_profile = _defect_profile(meta)
    if defect_profile is not None:
        result_data["defect_profile"] = defect_profile

    # C. Test gaps + security signals
    result_data["test_gap"] = await _check_test_gap(session, repo_id, target)
    result_data["security_signals"] = await _get_security_signals(session, repo_id, target)

    capped = getattr(meta, "commit_count_capped", False)
    capped_note = " (history truncated — actual count may be higher)" if capped else ""
    result_data["commit_count_capped"] = capped

    bus_note = ""
    if bus_factor == 1 and (meta.commit_count_total or 0) > 20:
        bus_note = f", bus factor risk (sole maintainer: {owner})"

    # NOTE: risk_summary is built here but dependents_count may be updated
    # later by cross-repo enrichment. We store dep_count now and let the
    # outer function rebuild the summary after enrichment if needed.
    result_data["_base_dep_count"] = dep_count
    # Lead with the bug-fix history when there is any. The summary used to open
    # on a churn percentile even where risk_type said "bug-prone", so the first
    # thing an agent read disagreed with the classification beside it. Counted
    # fixes are the better grounded defect signal, so they go first and churn
    # keeps its place as the next clause.
    result_data["risk_summary"] = (
        f"{target} — {_fix_clause(defect_profile)}"
        f"hotspot score {hotspot_score:.0%} ({trend}), "
        f"{dep_count} dependents, {risk_type}, {change_pattern}, "
        f"{len(co_changes)} co-change partners, owned {pct:.0%} by {owner}"
        f"{bus_note}{capped_note}"
    )

    return result_data

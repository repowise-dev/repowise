"""Git-history fields and summary clauses of a get_risk target card.

Pure functions over a loaded ``GitMetadata`` row: no queries.
"""

from __future__ import annotations

import contextlib
import json
import math
from datetime import UTC, datetime
from typing import Any

from repowise.core.ingestion.git_indexer._constants import PRIOR_DEFECT_WINDOW_LABEL

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

    *team_size* is the repo's active-contributor count (90d). On a small team
    a single-author file is the expected operating model, so
    ``bus-factor-risk`` is reserved for hotspot files there; ``None`` means
    unknown. ``bug-prone`` reads the counted, windowed fix history
    (``prior_defect_count``) and its decayed form (``bug_magnet``); an index
    without that rollup reports 0/None and falls through to the next rule.
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


def fix_annotation(meta: Any, now: datetime | None = None) -> dict | None:
    """The shared count/age/magnet fix block used by context and risk.

    Centralized so the two tools agree on the wire format and the age contract
    is enforced once: the ``bug_magnet`` flag rides on the age and is never
    emitted alone. ``bug_magnet`` is a claim about RECENT fix pressure, so with
    no timestamp to anchor it the same word would describe a file fixed four
    times last month and one fixed four times two years ago.

    Read off the ``GitMetadata`` row the caller already loaded: no query.
    """
    count = getattr(meta, "prior_defect_count", 0) or 0
    if count <= 0:
        return None

    # The window rides with the count: a bare total beside 90-day churn reads
    # as a 90-day figure.
    out: dict[str, Any] = {"fix_count": count, "window": PRIOR_DEFECT_WINDOW_LABEL}
    last_fix_at = getattr(meta, "last_fix_at", None)
    if isinstance(last_fix_at, datetime):
        # Rows are stored naive-UTC; compare on the same footing.
        ref = (
            datetime.now(UTC)
            if now is None
            else (now if now.tzinfo else now.replace(tzinfo=UTC))
        )
        moment = last_fix_at if last_fix_at.tzinfo else last_fix_at.replace(tzinfo=UTC)
        out["last_fix_days_ago"] = max(0, (ref - moment).days)
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
        f"{n} bug fix{'es' if n != 1 else ''} in {profile['window']}, "
        f"last {profile['last_fix_days_ago']}d before the indexed commit{magnet}, "
    )


def _churn_clause(churn_percentile: float, is_hotspot: bool, trend: str) -> str:
    """Churn clause for ``risk_summary``, or empty when the file is not a hotspot.

    ``churn_percentile`` is a rank (0 to 1), not a risk, so the clause states it
    as "top N%" and only for files the stored ``is_hotspot`` flag marks.
    Trailing separator included, as in ``_fix_clause``.
    """
    if not is_hotspot:
        return ""
    # Round before the ceiling: 1 - 0.86 is 0.14000000000000001 in floats, and
    # that would otherwise print as top 15%.
    top_pct = max(1, math.ceil(round((1.0 - churn_percentile) * 100, 6)))
    return f"churn hotspot, top {top_pct}% ({trend}), "


def _defect_profile(meta: Any, now: datetime | None = None) -> dict | None:
    """What this file's counted bug fixes say about it, or ``None`` for silence.

    Built from the fix-event rollup on the loaded ``GitMetadata`` row. Silent
    when the file has no counted fixes. Every field is aggregate: no inducing
    commit is named. The approximation caveat on ``top_symbols`` lives once in
    ``get_risk``'s docstring, not per row.
    """
    profile = fix_annotation(meta, now=now)
    if profile is None:
        return None

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


def _owner_clause(owner: str, commit_pct: float, line_pct: float | None) -> str:
    """Name the owner with the share each figure actually measures.

    With blame, the owner is the top author of current lines, which need not
    be the top committer, so both shares are stated and neither is called the
    other.
    """
    if line_pct is None:
        return f"primary owner {owner} ({commit_pct:.0%} of commits)"
    return f"{owner} wrote {line_pct:.0%} of current lines ({commit_pct:.0%} of commits)"


def _history_tier_card(target: str, meta: Any, security_signals: list[dict]) -> dict:
    """The card for a file indexed at the history tier (``meta.history_only``).

    A non-code file keeps commit counts, windows and authorship, but no churn
    percentile, hotspot flag, co-change partners or fix rollup, so the scored
    card would print zeros nothing measured. This one carries only what was.
    """
    commits = meta.commit_count_total or 0
    last = meta.last_commit_at.isoformat() if meta.last_commit_at else None
    owner = meta.primary_owner_name or "unknown"
    pct = meta.primary_owner_commit_pct or 0.0
    return {
        "target": target,
        "signal_tier": "history",
        "signal_tier_reason": (
            "not a parsed code file: only commit history is indexed, so hotspot, "
            "co-change and risk classification do not apply"
        ),
        "commit_count_total": commits,
        "commit_count_90d": meta.commit_count_90d or 0,
        "last_commit_at": last,
        "trend": _compute_trend(meta),
        "primary_owner": owner,
        "owner_pct": pct,
        "contributor_count": meta.contributor_count or 0,
        "security_signals": security_signals,
        # Workspace enrichment adds its rows here; co-change covers code files only.
        "relationship_analysis": {
            "co_change": {
                "status": "unavailable",
                "scope": "git_history",
                "reason": "history-tier file",
            }
        },
        "risk_summary": (
            f"{target}: history only, {commits} commits, last {last[:10] if last else 'unknown'}, "
            f"{_owner_clause(owner, pct, None)}"
        ),
    }


def history_fields(meta: Any, dep_count: int, team_size: int | None) -> dict[str, Any]:
    """Ownership, trend, classification and change-shape fields, in wire order."""
    fields: dict[str, Any] = {
        "primary_owner": meta.primary_owner_name or "unknown",
        "owner_pct": meta.primary_owner_commit_pct or 0.0,
        "owner_line_pct": getattr(meta, "primary_owner_line_pct", None),
        "recent_owner": getattr(meta, "recent_owner_name", None),
        "recent_owner_pct": getattr(meta, "recent_owner_commit_pct", None),
        "bus_factor": getattr(meta, "bus_factor", 0) or 0,
        "contributor_count": getattr(meta, "contributor_count", 0) or 0,
        "trend": _compute_trend(meta),
        "risk_type": _classify_risk_type(meta, dep_count, team_size),
        "change_pattern": _derive_change_pattern(_load_commit_categories(meta)),
        "change_magnitude": {
            "lines_added_90d": getattr(meta, "lines_added_90d", 0) or 0,
            "lines_deleted_90d": getattr(meta, "lines_deleted_90d", 0) or 0,
            "avg_commit_size": round(getattr(meta, "avg_commit_size", 0.0) or 0.0, 1),
        },
    }
    original_path = getattr(meta, "original_path", None)
    if original_path:
        fields["original_path"] = original_path
    merge_commit_count = getattr(meta, "merge_commit_count_90d", 0) or 0
    if merge_commit_count > 0:
        fields["merge_commit_count_90d"] = merge_commit_count
    return fields


def risk_summary(
    target: str,
    meta: Any,
    card: dict[str, Any],
    defect_profile: dict | None,
    dep_count: int,
    co_changes_total: int,
) -> str:
    """One-line ``risk_summary`` for a scored card.

    Leads with counted bug fixes, the better grounded defect signal, so the
    first clause agrees with ``risk_type``; churn follows for hotspot files.
    """
    capped_note = (
        " (history truncated — actual count may be higher)" if card["commit_count_capped"] else ""
    )
    owner = card["primary_owner"]
    bus_note = ""
    if card["bus_factor"] == 1 and (meta.commit_count_total or 0) > 20:
        bus_note = f", bus factor risk (sole maintainer: {owner})"
    return (
        f"{target} — {_fix_clause(defect_profile)}"
        f"{_churn_clause(card['hotspot_score'], card['is_hotspot'], card['trend'])}"
        f"{dep_count} direct dependents, {card['risk_type']}, {card['change_pattern']}, "
        f"{co_changes_total} co-change partners, "
        f"{_owner_clause(owner, card['owner_pct'], card['owner_line_pct'])}"
        f"{bus_note}{capped_note}"
    )

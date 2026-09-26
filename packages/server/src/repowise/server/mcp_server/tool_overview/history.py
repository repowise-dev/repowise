"""Git-derived overview blocks: repo-wide git health and the ownership map."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _build_git_health(all_git: list) -> dict[str, Any]:
    """Repo-wide git health summary (hotspots, bus factor, churn trend, top modules)."""
    if not all_git:
        return {}

    bus_factors = [getattr(g, "bus_factor", 0) or 0 for g in all_git]
    return {
        # Files with git history, not the parsed total: vendored, generated
        # and new files have none, so the two counts can differ.
        "files_git_attributed": len(all_git),
        "hotspot_count": sum(1 for g in all_git if g.is_hotspot),
        "avg_bus_factor": round(sum(bus_factors) / len(bus_factors), 1),
        "files_with_bus_factor_1": sum(1 for b in bus_factors if b == 1),
        "churn_trend": _churn_trend(all_git),
        "top_churn_modules": _top_churn_modules(all_git),
    }


def _churn_trend(all_git: list) -> str:
    """Last-30-day commit rate against the 60 days before it."""
    c30_total = sum(g.commit_count_30d or 0 for g in all_git)
    baseline = sum(g.commit_count_90d or 0 for g in all_git) - c30_total
    if baseline <= 0:
        return "increasing" if c30_total > 0 else "stable"
    ratio = (c30_total / 30.0) / (baseline / 60.0)
    if ratio > 1.5:
        return "increasing"
    return "decreasing" if ratio < 0.5 else "stable"


def _top_churn_modules(all_git: list) -> list[str]:
    """Five busiest modules by 90-day commits, keyed on the first two path segments."""
    module_churn: Counter = Counter()
    for g in all_git:
        module_churn["/".join(g.file_path.split("/")[:2])] += g.commit_count_90d or 0
    return [m for m, _ in module_churn.most_common(5) if module_churn[m] > 0]


def _owner_display_name(name: str | None, email: str) -> str:
    """A privacy-safe display name for a contributor — never the raw email.

    Prefers the recorded ``primary_owner_name``; when absent — or when that name
    is itself an address (bot/CI commits, a misconfigured ``user.name``) —
    derives a conservative label from the email's local part (e.g. ``jane.doe``
    from ``jane.doe@example.com``) so the address itself is never surfaced.
    """
    if name and name.strip() and "@" not in name:
        return name.strip()
    local = (email or "").split("@", 1)[0].strip()
    return local or "unknown"


def _build_knowledge_map(all_git: list) -> dict[str, Any]:
    """Top owners aggregated across all indexed files."""
    if not all_git:
        return {}

    # Aggregate on email (the stable identity key) but never surface it — the
    # payload emits a display name only, to keep contributor emails private.
    owner_file_count: dict[str, int] = defaultdict(int)
    owner_name: dict[str, str] = {}
    for g in all_git:
        email = g.primary_owner_email or ""
        if email:
            owner_file_count[email] += 1
            owner_name.setdefault(email, _owner_display_name(g.primary_owner_name, email))

    total_files = len(all_git) or 1
    # Top 3 only: orientation needs the first few names; per-file ownership
    # belongs to get_risk / get_context(include=["ownership"]).
    top_owners = sorted(
        [
            {
                "name": owner_name.get(email) or _owner_display_name(None, email),
                "files_owned": count,
                "percentage": round(count / total_files * 100.0, 1),
            }
            for email, count in owner_file_count.items()
        ],
        key=lambda x: -x["files_owned"],
    )[:3]

    return {"top_owners": top_owners}

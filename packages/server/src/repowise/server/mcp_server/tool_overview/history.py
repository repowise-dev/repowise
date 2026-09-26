"""Git-derived overview blocks: repo-wide git health and the ownership map."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _build_git_health(all_git: list) -> dict[str, Any]:
    """Repo-wide git health summary (hotspots, bus factor, churn trend, top modules)."""
    if not all_git:
        return {}

    hotspot_count = sum(1 for g in all_git if g.is_hotspot)
    bus_factors = [getattr(g, "bus_factor", 0) or 0 for g in all_git]
    avg_bus = sum(bus_factors) / len(bus_factors) if bus_factors else 0
    bf1 = sum(1 for b in bus_factors if b == 1)
    c30_total = sum(g.commit_count_30d or 0 for g in all_git)
    c90_total = sum(g.commit_count_90d or 0 for g in all_git)
    baseline = c90_total - c30_total
    if baseline > 0:
        ratio = (c30_total / 30.0) / (baseline / 60.0)
        churn_trend = "increasing" if ratio > 1.5 else ("decreasing" if ratio < 0.5 else "stable")
    else:
        churn_trend = "increasing" if c30_total > 0 else "stable"
    # Top churn modules (group by first directory component)
    module_churn: Counter = Counter()
    for g in all_git:
        parts = g.file_path.split("/")
        mod = parts[0] if len(parts) == 1 else "/".join(parts[:2])
        module_churn[mod] += g.commit_count_90d or 0
    top_modules = [m for m, _ in module_churn.most_common(5) if module_churn[m] > 0]

    return {
        # Files that carry git history (churn/ownership), NOT the parsed file
        # total — a repo can parse more files than git attributes (vendored,
        # generated, or newly added files have no 90-day history). Named
        # explicitly so the two counts don't read as a discrepancy.
        "files_git_attributed": len(all_git),
        "hotspot_count": hotspot_count,
        "avg_bus_factor": round(avg_bus, 1),
        "files_with_bus_factor_1": bf1,
        "churn_trend": churn_trend,
        "top_churn_modules": top_modules,
    }


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
    """Top owners and knowledge silos aggregated across all indexed files."""
    if not all_git:
        return {}

    # Aggregate on email (the stable identity key) but never surface it — the
    # payload emits a display name only, to keep contributor emails private.
    owner_file_count: dict[str, int] = defaultdict(int)
    owner_pct_sum: dict[str, float] = defaultdict(float)
    owner_name: dict[str, str] = {}
    for g in all_git:
        email = g.primary_owner_email or ""
        if email:
            owner_file_count[email] += 1
            owner_pct_sum[email] += float(g.primary_owner_commit_pct or 0.0)
            owner_name.setdefault(email, _owner_display_name(g.primary_owner_name, email))

    total_files = len(all_git) or 1
    # Top 3 only: get_overview is orientation, and "who do I ask" is answered
    # by the first few names. Per-file ownership questions belong to
    # get_risk / get_context(include=["ownership"]). The old payload also
    # carried a knowledge_silos file list here — dropped: it duplicated
    # get_risk's per-file ownership signal and gave an orienting agent
    # nothing actionable.
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

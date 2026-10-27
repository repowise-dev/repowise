"""Git-derived overview blocks: repo-wide git health and the ownership map."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

#: Riskiest files the overview names; get_risk / get_health carry the rest.
_RISKIEST_FILES = 5


def _build_git_health(all_git: list, repo_root: Path | str | None = None) -> dict[str, Any]:
    """Repo-wide git health: hotspot count, churn trend and the riskiest files."""
    if not all_git:
        return {}
    return {
        "hotspot_count": sum(1 for g in all_git if g.is_hotspot),
        "churn_trend": _churn_trend(all_git),
        "riskiest_files": _riskiest_files(all_git, repo_root),
    }


def _riskiest_files(all_git: list, repo_root: Path | str | None) -> list[str]:
    """Hotspots and bug magnets in the order the generated CLAUDE.md lists them.

    Bug magnets first, then fix mass, then churn, as ``files that need care``
    ranks them in the editor files. A row can outlive its file when the
    deleted-file prune skips a run, so with *repo_root* only live files count.
    """
    candidates = sorted(
        (g for g in all_git if g.is_hotspot or getattr(g, "bug_magnet", False)),
        key=lambda g: (
            not getattr(g, "bug_magnet", False),
            -(getattr(g, "fix_mass", 0) or 0),
            -(getattr(g, "churn_percentile", 0) or 0),
            g.file_path,
        ),
    )
    root = Path(repo_root) if repo_root else None
    out: list[str] = []
    for g in candidates:
        if len(out) >= _RISKIEST_FILES:
            break
        if root is None or (root / g.file_path).exists():
            out.append(g.file_path)
    return out


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

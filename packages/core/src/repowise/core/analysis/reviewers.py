"""Reviewer suggestions for a set of changed files, folded from git metadata rows.

Sync, no I/O, no clock. The composite score blends three signals the indexer
already computed:

  - Ownership: appearance in ``top_authors_json`` of the touched files,
    weighted by share of file commits.
  - Co-change: ownership of files that historically co-change with the
    touched paths (``co_change_partners_json``).
  - Recency: weight commit count by the file's 90-day activity so people
    who *just* worked here outrank people who touched the file in 2018.

Rows may be dicts, dataclasses or ORM rows; JSON columns may be text or
already decoded.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from repowise.core.analysis.health.rows import field, json_field
from repowise.core.co_change import parse_partners

# Tunable weights.
_W_DIRECT = 1.0
_W_COCHANGE = 0.5
_W_RECENT = 0.4

#: Strongest co-change partners followed per touched file, to keep noise low.
PARTNERS_PER_FILE = 5


def cochange_paths(direct_rows: Iterable[Any]) -> set[str]:
    """Paths whose owners also count: each touched file's strongest partners."""
    return {
        p.file_path
        for m in direct_rows
        for p in parse_partners(field(m, "co_change_partners_json"))[:PARTNERS_PER_FILE]
    }


def suggest_reviewers(
    direct_rows: Iterable[Any], cochange_rows: Iterable[Any], *, limit: int = 10
) -> list[dict[str, Any]]:
    """Top *limit* reviewers, best first, as ``ReviewerSuggestion``-shaped dicts.

    ``direct_rows`` are the git rows of the touched files; ``cochange_rows``
    those of :func:`cochange_paths`. Each needs ``file_path``,
    ``top_authors_json`` and ``commit_count_90d``.
    """
    tally: dict[str, dict] = {}

    def _bump(
        name: str,
        email: str | None,
        *,
        score: float,
        recent: int,
        path: str,
        reason: str,
        owned: bool,
    ) -> None:
        key = (email or "").strip().lower() or f"name:{name.strip()}"
        slot = tally.setdefault(
            key,
            {
                "name": name,
                "email": email,
                "score": 0.0,
                "recent_commits": 0,
                "owned_paths": set(),
                "co_change_paths": set(),
                "reasons": set(),
            },
        )
        slot["score"] += score
        slot["recent_commits"] += recent
        if owned:
            slot["owned_paths"].add(path)
        else:
            slot["co_change_paths"].add(path)
        slot["reasons"].add(reason)

    def _process(rows: Iterable[Any], *, weight: float, owned: bool, reason: str) -> None:
        for m in rows:
            authors = json_field(m, "top_authors_json", [])
            total = sum(int(a.get("commit_count", 0)) for a in authors) or 1
            commits_90d = field(m, "commit_count_90d") or 0
            for a in authors:
                cnt = int(a.get("commit_count", 0))
                share = cnt / total
                # Recency: commits_90d weighted by share is a rough estimate
                # of how much each author contributed recently.
                recent = int(commits_90d * share)
                score = weight * share + _W_RECENT * (recent / max(commits_90d, 1))
                _bump(
                    a.get("name", ""),
                    a.get("email") or None,
                    score=score,
                    recent=recent,
                    path=field(m, "file_path"),
                    reason=reason,
                    owned=owned,
                )

    _process(direct_rows, weight=_W_DIRECT, owned=True, reason="touched")
    _process(cochange_rows, weight=_W_COCHANGE, owned=False, reason="co-change history")

    suggestions = [
        {
            "name": slot["name"],
            "email": slot["email"],
            "score": round(slot["score"], 4),
            "recent_commits": slot["recent_commits"],
            "owned_paths": sorted(slot["owned_paths"])[:10],
            "co_change_paths": sorted(slot["co_change_paths"])[:10],
            "reasons": sorted(slot["reasons"]),
        }
        for slot in tally.values()
    ]
    suggestions.sort(key=lambda s: s["score"], reverse=True)
    return suggestions[:limit]


__all__ = ["PARTNERS_PER_FILE", "cochange_paths", "suggest_reviewers"]

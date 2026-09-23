"""Knowledge silos and onboarding targets, folded from rows the caller fetched.

Sync, no I/O, no clock. The server reads these rows with SQL; any other
producer can hand in the same keys from its own store. Rows may be dicts,
dataclasses or ORM/SQL rows (read through :func:`..health.rows.field`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.analysis.health.rows import field

#: A file is a silo when its primary owner holds more than this share of commits.
SILO_OWNER_PCT = 0.8

#: Onboarding targets returned per repository.
ONBOARDING_LIMIT = 10


def rank_silos(git_rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Files whose primary owner holds more than :data:`SILO_OWNER_PCT`, riskiest first.

    Each row carries ``file_path``, ``primary_owner_email``,
    ``primary_owner_commit_pct``, ``commit_count_90d`` and ``is_hotspot``; rows at
    or under the threshold are dropped, so a caller may pass every file.
    """
    kept = [r for r in git_rows if (field(r, "primary_owner_commit_pct") or 0.0) > SILO_OWNER_PCT]
    # Activity first, concentration second: sole ownership of a file nobody
    # touches is a fact, not a risk, so previews should lead with live code.
    kept.sort(
        key=lambda r: (
            not field(r, "is_hotspot"),
            field(r, "commit_count_90d") is None,
            -(field(r, "commit_count_90d") or 0),
            -field(r, "primary_owner_commit_pct"),
        )
    )
    return [
        {
            "file_path": field(r, "file_path"),
            "owner_email": field(r, "primary_owner_email") or "",
            "owner_pct": round(float(field(r, "primary_owner_commit_pct")), 3),
            "commit_count_90d": int(field(r, "commit_count_90d") or 0),
            "is_hotspot": bool(field(r, "is_hotspot")),
        }
        for r in kept
    ]


def onboarding_targets(
    file_nodes: Iterable[Any],
    doc_words: Mapping[str, int],
    *,
    limit: int = ONBOARDING_LIMIT,
) -> list[dict[str, Any]]:
    """High-centrality files carrying the least documentation.

    ``file_nodes`` are non-test graph nodes with ``node_id`` and ``pagerank``;
    nodes without a positive pagerank are skipped. ``doc_words`` maps a path to
    its file page's size, 0 or absent when undocumented. Sorted by that size,
    then pagerank descending; ties keep input order.
    """
    candidates = [
        {
            "path": field(n, "node_id"),
            "pagerank": field(n, "pagerank"),
            "doc_words": doc_words.get(field(n, "node_id"), 0),
        }
        for n in file_nodes
        if (field(n, "pagerank") or 0.0) > 0.0
    ]
    candidates.sort(key=lambda c: (c["doc_words"], -c["pagerank"]))
    return candidates[:limit]


__all__ = [
    "ONBOARDING_LIMIT",
    "SILO_OWNER_PCT",
    "onboarding_targets",
    "rank_silos",
]

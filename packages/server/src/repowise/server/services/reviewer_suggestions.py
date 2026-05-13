"""Reviewer suggestion service.

Given a set of changed file paths, suggest who should review the PR. The
composite score blends three signals — all already computed during indexing:

  - Ownership: appearance in ``top_authors_json`` of the touched files,
    weighted by share of file commits.
  - Co-change: ownership of files that historically co-change with the
    touched paths (``co_change_partners_json``).
  - Recency: weight commit count by the file's 90-day activity so people
    who *just* worked here outrank people who touched the file in 2018.

The output is intentionally short (top 10) — PRs with 30 suggested reviewers
are no better than zero.
"""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GitMetadata
from repowise.server.schemas import ReviewerSuggestion


# Tunable weights.
_W_DIRECT = 1.0
_W_COCHANGE = 0.5
_W_RECENT = 0.4


async def suggest_reviewers(
    session: AsyncSession,
    repo_id: str,
    paths: list[str],
    limit: int = 10,
) -> list[ReviewerSuggestion]:
    if not paths:
        return []

    # 1) Direct ownership for the touched paths.
    direct_rows = (
        await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path.in_(paths),
            )
        )
    ).scalars().all()

    # 2) For each touched path, look up its co-change partners (cap to 5
    # strongest per file to keep the suggestion noise low) and fetch their
    # GitMetadata as well.
    cochange_paths: set[str] = set()
    cochange_partners: dict[str, list[str]] = defaultdict(list)
    for m in direct_rows:
        try:
            partners = json.loads(m.co_change_partners_json or "[]")
        except json.JSONDecodeError:
            partners = []
        partners.sort(key=lambda p: p.get("co_change_count", 0), reverse=True)
        for p in partners[:5]:
            other = p.get("file_path")
            if other:
                cochange_paths.add(other)
                cochange_partners[other].append(m.file_path)

    cochange_rows = []
    if cochange_paths:
        cochange_rows = (
            await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path.in_(cochange_paths),
                )
            )
        ).scalars().all()

    # Per-author tally.
    tally: dict[str, dict] = {}

    def _bump(name: str, email: str | None, *, score: float, recent: int, path: str, reason: str, owned: bool) -> None:
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

    def _process(rows, *, weight: float, owned: bool, reason: str) -> None:
        for m in rows:
            try:
                authors = json.loads(m.top_authors_json or "[]")
            except json.JSONDecodeError:
                continue
            total = sum(int(a.get("commit_count", 0)) for a in authors) or 1
            commits_90d = m.commit_count_90d or 0
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
                    path=m.file_path,
                    reason=reason,
                    owned=owned,
                )

    _process(direct_rows, weight=_W_DIRECT, owned=True, reason="touched")
    _process(cochange_rows, weight=_W_COCHANGE, owned=False, reason="co-change history")

    suggestions = []
    for slot in tally.values():
        suggestions.append(
            ReviewerSuggestion(
                name=slot["name"],
                email=slot["email"],
                score=round(slot["score"], 4),
                recent_commits=slot["recent_commits"],
                owned_paths=sorted(slot["owned_paths"])[:10],
                co_change_paths=sorted(slot["co_change_paths"])[:10],
                reasons=sorted(slot["reasons"]),
            )
        )
    suggestions.sort(key=lambda s: s.score, reverse=True)
    return suggestions[:limit]

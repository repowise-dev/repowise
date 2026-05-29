"""FULL-tier enrichment helpers: blame ownership, commit significance,
rename detection, and churn percentiles.

These are pure functions (no GitIndexer instance state) so each can be unit
tested in isolation. The cheap baseline (commit counts, authors, temporal
score) lives in :mod:`file_history`; the expensive blame call and the
significance/percentile logic that only the FULL tier needs live here.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ._constants import (
    _DECISION_SIGNAL_WORDS,
    _HARD_SKIP_PREFIXES,
    _MIN_MESSAGE_LEN,
    _SKIP_AUTHORS,
    _SOFT_SKIP_PREFIXES,
)

__all__ = [
    "compute_percentiles",
    "detect_original_path",
    "get_blame_ownership",
    "is_significant_commit",
]


def detect_original_path(repo: Any, file_path: str, commit_limit: int) -> str | None:
    """If --follow reveals the file was renamed, return its earliest prior path."""
    try:
        raw = repo.git.log(
            "--follow",
            f"-{commit_limit}",
            "--format=",
            "--name-only",
            "--",
            file_path,
        )
    except Exception:
        return None

    # Paths appear newest-first; the last distinct path is the original.
    prev_path: str | None = None
    for line in raw.splitlines():
        p = line.strip()
        if p and p != file_path:
            prev_path = p  # keep overwriting — last one is oldest
    return prev_path


def get_blame_ownership(repo: Any, file_path: str) -> tuple[str | None, str | None, float | None]:
    """Compute primary owner from git blame (who wrote the most lines)."""
    try:
        blame = repo.blame("HEAD", file_path)
    except Exception:
        return None, None, None

    line_counts: Counter[str] = Counter()
    emails: dict[str, str] = {}
    total_lines = 0

    for commit, lines in blame:
        name = commit.author.name or "unknown"
        count = len(lines)
        line_counts[name] += count
        total_lines += count
        if name not in emails and commit.author.email:
            emails[name] = commit.author.email

    if not line_counts or total_lines == 0:
        return None, None, None

    top_name = line_counts.most_common(1)[0][0]
    pct = line_counts[top_name] / total_lines
    return top_name, emails.get(top_name), pct


def is_significant_commit(message: str, author: str) -> bool:
    """Return True if the commit is considered significant.

    Filtering rules:
    1. Always skip messages shorter than _MIN_MESSAGE_LEN characters.
    2. Always skip merge commits and bot authors (no useful signal).
    3. Conventional-commit prefixes (chore:, ci:, style:, build:,
       release:, Bump) are normally skipped — UNLESS the message also
       contains a decision-signal keyword (e.g. "build: migrate from
       webpack to vite").  This rescues architecturally meaningful
       commits that happen to use a low-signal prefix.
    """
    msg = message.strip()
    if len(msg) < _MIN_MESSAGE_LEN:
        return False
    # Always skip merge commits
    for prefix in _HARD_SKIP_PREFIXES:
        if msg.startswith(prefix):
            return False
    # Always skip bot authors
    author_lower = author.lower()
    for skip in _SKIP_AUTHORS:
        if skip in author_lower:
            return False
    # Soft-skip conventional prefixes unless decision signal present
    for prefix in _SOFT_SKIP_PREFIXES:
        if msg.startswith(prefix):
            msg_lower = msg.lower()
            return any(word in msg_lower for word in _DECISION_SIGNAL_WORDS)
    return True


def compute_percentiles(metadata_list: list[dict]) -> None:
    """Compute churn_percentile and is_hotspot. Mutates in place.

    Primary sort key is temporal_hotspot_score (exponentially decayed churn);
    commit_count_90d is used as a tiebreak, matching the SQL PERCENT_RANK path.
    """
    if not metadata_list:
        return

    # Sort by temporal_hotspot_score (primary) then commit_count_90d (tiebreak)
    sorted_by_churn = sorted(
        range(len(metadata_list)),
        key=lambda i: (
            metadata_list[i].get("temporal_hotspot_score") or 0.0,
            metadata_list[i].get("commit_count_90d", 0),
        ),
    )

    total = len(metadata_list)
    for rank, idx in enumerate(sorted_by_churn):
        metadata_list[idx]["churn_percentile"] = rank / total if total > 0 else 0.0

    # Hotspot: top 25% churn (i.e., churn_percentile >= 0.75)
    for meta in metadata_list:
        commit_90d = meta.get("commit_count_90d", 0)
        churn_pct = meta.get("churn_percentile", 0.0)
        if churn_pct >= 0.75 and commit_90d > 0:
            meta["is_hotspot"] = True

    # change_entropy percentile (mirrors churn_percentile). Rank ONLY files
    # that carry a positive entropy signal; files with zero entropy — every
    # file on the ESSENTIAL tier, plus FULL-tier files that only ever changed
    # alone — keep pct 0.0 so the change_entropy biomarker stays silent. (A
    # naive rank-everything would hand the topmost zero-entropy file a high
    # percentile when most files are zero.)
    for meta in metadata_list:
        meta.setdefault("change_entropy_pct", 0.0)
    entropy_idxs = [
        i for i in range(total) if (metadata_list[i].get("change_entropy") or 0.0) > 0.0
    ]
    n_ent = len(entropy_idxs)
    if n_ent > 0:
        entropy_idxs.sort(key=lambda i: metadata_list[i].get("change_entropy") or 0.0)
        for rank, idx in enumerate(entropy_idxs):
            metadata_list[idx]["change_entropy_pct"] = rank / n_ent

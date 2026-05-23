"""Per-file git history indexing — the ESSENTIAL baseline plus optional
FULL-tier blame enrichment.

``index_file`` parses a single file's commit history (from a precomputed
commit index when available, else a per-file ``git log``) and derives commit
counts, authorship, line churn, the temporal hotspot score, and significant
commits. The expensive ``git blame`` ownership pass is gated by
*include_blame* so the ESSENTIAL tier can skip it.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from ._constants import (
    _COMMIT_CATEGORIES,
    _MAX_BLAME_SIZE_BYTES,
    _MAX_SIGNIFICANT_COMMITS,
    _MAX_TOP_AUTHORS,
    _PR_NUMBER_RE,
    HOTSPOT_HALFLIFE_DAYS,
)
from .enrich import detect_original_path, get_blame_ownership, is_significant_commit
from .records import _CommitRec, _extract_rename_paths

logger = structlog.get_logger(__name__)

__all__ = ["index_file", "new_meta"]


def new_meta(file_path: str) -> dict[str, Any]:
    """Return the default metadata dict for *file_path* (all fields zeroed)."""
    return {
        "file_path": file_path,
        "commit_count_total": 0,
        "commit_count_90d": 0,
        "commit_count_30d": 0,
        "commit_count_capped": False,
        "first_commit_at": None,
        "last_commit_at": None,
        "primary_owner_name": None,
        "primary_owner_email": None,
        "primary_owner_commit_pct": None,
        "top_authors_json": "[]",
        "significant_commits_json": "[]",
        "co_change_partners_json": "[]",
        "commit_categories_json": "{}",
        "is_hotspot": False,
        "is_stable": False,
        "churn_percentile": 0.0,
        "age_days": 0,
        # Phase 2 fields
        "lines_added_90d": 0,
        "lines_deleted_90d": 0,
        "avg_commit_size": 0.0,
        "recent_owner_name": None,
        "recent_owner_commit_pct": None,
        "bus_factor": 0,
        "contributor_count": 0,
        # Phase 3 fields
        "original_path": None,
        "merge_commit_count_90d": 0,
        # Temporal hotspot score (exponentially decayed churn)
        "temporal_hotspot_score": 0.0,
    }


def _parse_per_file_log(
    repo: Any,
    file_path: str,
    *,
    commit_limit: int,
    follow_renames: bool,
) -> tuple[list[_CommitRec], str | None]:
    """Run a per-file ``git log --numstat`` and parse it into commit records.

    Returns ``(commits, original_path)``. Only used when no precomputed commit
    index is available (i.e. when rename-tracking is on).
    """
    log_args: list[str] = []
    if follow_renames:
        log_args.append("--follow")
    log_args += [
        f"-{commit_limit}",
        "--numstat",
        "--format=%x00%H%x1f%an%x1f%ae%x1f%ct%x1f%P%x1f%s",
        "--",
        file_path,
    ]
    try:
        raw = repo.git.log(*log_args)
    except Exception:
        return [], None

    if not raw.strip():
        return [], None

    known_paths: set[str] = {file_path}
    orig_path: str | None = None
    if follow_renames:
        orig_path = detect_original_path(repo, file_path, commit_limit)
        if orig_path:
            known_paths.add(orig_path)

    commits: list[_CommitRec] = []
    current: _CommitRec | None = None
    for line in raw.splitlines():
        if line.startswith("\x00"):
            parts = line.lstrip("\x00").split("\x1f")
            if len(parts) >= 6:
                sha, an, ae, ct, parents, subj = parts[:6]
                try:
                    ts = int(ct)
                except ValueError:
                    ts = 0
                current = _CommitRec(
                    sha=sha,
                    author_name=an or "unknown",
                    author_email=ae,
                    ts=ts,
                    is_merge=len(parents.split()) > 1,
                    subject=subj,
                )
                commits.append(current)
        elif current is not None and line.strip():
            numstat_parts = line.split("\t")
            if len(numstat_parts) >= 3:
                stat_path = numstat_parts[2]
                match_path = stat_path
                if "=>" in stat_path:
                    _old, _new = _extract_rename_paths(stat_path, known_paths)
                    match_path = _new or stat_path
                if match_path in known_paths or match_path == file_path:
                    try:
                        current.added += (
                            int(numstat_parts[0]) if numstat_parts[0] != "-" else 0
                        )
                        current.deleted += (
                            int(numstat_parts[1]) if numstat_parts[1] != "-" else 0
                        )
                    except ValueError:
                        pass
    return commits, orig_path


def index_file(
    repo: Any,
    file_path: str,
    *,
    repo_path: Path,
    commit_limit: int,
    follow_renames: bool,
    include_blame: bool = True,
    precomputed_commits: list[_CommitRec] | None = None,
) -> dict:
    """Index a single file's git history. Runs in executor.

    When *precomputed_commits* is provided (the default when called from the
    batched full-repo path), the per-file ``git log`` subprocess is skipped
    entirely — eliminating the dominant process-spawn cost on large repos.

    *include_blame* gates the FULL-tier ``git blame`` ownership pass; the
    ESSENTIAL tier sets it False and falls back to commit-author ownership.
    """
    now = datetime.now(UTC)
    ninety_days_ago_ts = (now - timedelta(days=90)).timestamp()
    thirty_days_ago_ts = (now - timedelta(days=30)).timestamp()

    meta = new_meta(file_path)

    orig_path: str | None = None
    if precomputed_commits is not None:
        commits: list[_CommitRec] = precomputed_commits
    else:
        commits, orig_path = _parse_per_file_log(
            repo,
            file_path,
            commit_limit=commit_limit,
            follow_renames=follow_renames,
        )

    if not commits:
        return meta

    meta["commit_count_total"] = len(commits)
    meta["commit_count_capped"] = len(commits) >= commit_limit

    try:
        timestamps = [c.ts for c in commits if c.ts > 0]
        if timestamps:
            first_ts = min(timestamps)
            last_ts = max(timestamps)
            meta["first_commit_at"] = datetime.fromtimestamp(first_ts, tz=UTC)
            meta["last_commit_at"] = datetime.fromtimestamp(last_ts, tz=UTC)
            meta["age_days"] = (now - datetime.fromtimestamp(first_ts, tz=UTC)).days

        author_counts: Counter[str] = Counter()
        author_emails: dict[str, str] = {}
        recent_author_counts: Counter[str] = Counter()

        for c in commits:
            if c.ts >= ninety_days_ago_ts:
                meta["commit_count_90d"] += 1
                recent_author_counts[c.author_name] += 1
                meta["lines_added_90d"] += c.added
                meta["lines_deleted_90d"] += c.deleted
                if c.is_merge:
                    meta["merge_commit_count_90d"] += 1
            if c.ts >= thirty_days_ago_ts:
                meta["commit_count_30d"] += 1
            author_counts[c.author_name] += 1
            if c.author_name not in author_emails and c.author_email:
                author_emails[c.author_name] = c.author_email

        c90 = meta["commit_count_90d"]
        total_churn = meta["lines_added_90d"] + meta["lines_deleted_90d"]
        meta["avg_commit_size"] = total_churn / c90 if c90 > 0 else 0.0

        # Temporal hotspot score: exponentially decayed per-commit churn.
        _ln2 = math.log(2)
        temporal_score = 0.0
        for c in commits:
            age_days = max((now.timestamp() - c.ts) / 86400.0, 0.0)
            weight = math.exp(-_ln2 * age_days / HOTSPOT_HALFLIFE_DAYS)
            lines = min((c.added + c.deleted) / 100.0, 3.0)
            temporal_score += weight * lines
        meta["temporal_hotspot_score"] = temporal_score

        # Contributor count & bus factor
        meta["contributor_count"] = len(author_counts)
        total_commits = sum(author_counts.values())
        if total_commits > 0:
            threshold = total_commits * 0.8
            running = 0
            bus = 0
            for _name, cnt in author_counts.most_common():
                running += cnt
                bus += 1
                if running >= threshold:
                    break
            meta["bus_factor"] = bus

        # Top authors — keep a generous slice (50) so the API can show the
        # full contributor surface for a file.
        top_authors = []
        for name, count in author_counts.most_common(_MAX_TOP_AUTHORS):
            top_authors.append(
                {
                    "name": name,
                    "email": author_emails.get(name, ""),
                    "commit_count": count,
                }
            )
        meta["top_authors_json"] = json.dumps(top_authors)

        if top_authors:
            primary = top_authors[0]
            meta["primary_owner_name"] = primary["name"]
            meta["primary_owner_email"] = primary["email"]
            meta["primary_owner_commit_pct"] = (
                primary["commit_count"] / total_commits if total_commits > 0 else 0.0
            )

        # Recent owner (90d)
        if recent_author_counts:
            recent_top = recent_author_counts.most_common(1)[0]
            meta["recent_owner_name"] = recent_top[0]
            recent_total = sum(recent_author_counts.values())
            meta["recent_owner_commit_pct"] = (
                recent_top[1] / recent_total if recent_total > 0 else 0.0
            )

        # Blame ownership (FULL tier only; overrides author-based if available).
        # Skipped for large files — git blame is O(lines) and can block the
        # executor thread for many seconds on files > 100 KB.
        if include_blame:
            try:
                file_size = (repo_path / file_path).stat().st_size
                if file_size <= _MAX_BLAME_SIZE_BYTES:
                    blame_name, blame_email, blame_pct = get_blame_ownership(
                        repo, file_path
                    )
                    if blame_name:
                        meta["primary_owner_name"] = blame_name
                        meta["primary_owner_email"] = blame_email
                        meta["primary_owner_commit_pct"] = blame_pct
            except Exception:
                pass  # blame is best-effort

        # Significant commits + classification + PR extraction
        sig_commits: list[dict[str, Any]] = []
        sig_full = False
        category_counts: Counter[str] = Counter()
        for c in commits:
            msg = c.subject[:200]
            if not sig_full and is_significant_commit(msg, c.author_name):
                entry: dict[str, Any] = {
                    "sha": c.sha[:8],
                    "date": datetime.fromtimestamp(c.ts, tz=UTC).isoformat()
                    if c.ts
                    else "",
                    "message": msg,
                    "author": c.author_name,
                }
                pr_match = _PR_NUMBER_RE.search(msg)
                if pr_match:
                    pr_num = pr_match.group(1) or pr_match.group(2) or pr_match.group(3)
                    entry["pr_number"] = int(pr_num)
                sig_commits.append(entry)
                if len(sig_commits) >= _MAX_SIGNIFICANT_COMMITS:
                    sig_full = True
            # Classify ALL commits for accurate category ratios
            for cat, pattern in _COMMIT_CATEGORIES.items():
                if pattern.search(msg):
                    category_counts[cat] += 1
                    break

        meta["significant_commits_json"] = json.dumps(sig_commits)
        meta["commit_categories_json"] = json.dumps(dict(category_counts))

        # Original path detection (rename tracking) — reuse the result from the
        # known_paths seeding above to avoid a duplicate subprocess.
        if follow_renames and orig_path:
            meta["original_path"] = orig_path

        # Stable classification
        if meta["commit_count_total"] > 10 and meta["commit_count_90d"] == 0:
            meta["is_stable"] = True

    except Exception:
        logger.debug("git_indexer_partial_failure", file_path=file_path, exc_info=True)

    return meta

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
    _DECISION_SIGNAL_WORDS,
    _MAX_BLAME_SIZE_BYTES,
    _MAX_COMMIT_BODY_BYTES,
    _MAX_SIGNIFICANT_COMMITS,
    _MAX_TOP_AUTHORS,
    _PR_BODY_MARKERS,
    _PR_NUMBER_RE,
    HOTSPOT_HALFLIFE_DAYS,
)
from .enrich import detect_original_path, is_significant_commit
from .function_blame import (
    _MIN_COMMITS_FOR_BLAME,
    build_blame_index,
    ownership_from_blame,
)
from .identity import canonicalize_author_email
from .records import (
    _LOG_FORMAT,
    _RECORD_SEP,
    _CommitRec,
    _extract_rename_paths,
    _parse_commit_record,
)


def _body_carries_decision(subject: str, body: str) -> bool:
    """Whether a commit body is worth retaining for decision mining.

    The body is stored once per file the commit touched, so retaining it
    indiscriminately bloats the index. Keep it only when the subject/body shows
    decision intent — a decision-signal keyword or a PR-description marker — i.e.
    exactly the commits the PR / git-archaeology miners would consume.
    """
    if not body:
        return False
    blob = f"{subject}\n{body}".lower()
    if any(marker in blob for marker in _PR_BODY_MARKERS):
        return True
    return any(word in blob for word in _DECISION_SIGNAL_WORDS)


def _truncate_body(body: str) -> str:
    """Trim *body* to the byte ceiling, never splitting a UTF-8 sequence."""
    if not body:
        return ""
    encoded = body.encode("utf-8")
    if len(encoded) <= _MAX_COMMIT_BODY_BYTES:
        return body
    return encoded[:_MAX_COMMIT_BODY_BYTES].decode("utf-8", errors="ignore")


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
        # Prior-defect history: count of bug-fix commits touching this file in
        # the trailing PRIOR_DEFECT_WINDOW_DAYS window (anchored to as_of_ts when
        # set, so T0 benchmark scoring stays leakage-free). Consumed by the
        # ``prior_defect`` health biomarker; mirrors the benchmark's
        # prior-defects baseline definition (product == benchmark).
        "prior_defect_count": 0,
        # Agent provenance rollup: how much of this file's indexed history is
        # agent-attributed (local channels only — see agent_provenance module).
        # agent_authored_pct stays None when the file has no commits at all.
        "agent_commit_count": 0,
        "agent_authored_pct": None,
        "agent_tier_counts_json": "{}",
        # Temporal hotspot score (exponentially decayed churn)
        "temporal_hotspot_score": 0.0,
        # Change entropy (Hassan HCM) — populated repo-wide by the co-change
        # walk, percentile by enrich.compute_percentiles. Default 0.0 leaves
        # the signal silent on the ESSENTIAL tier / files that never co-changed.
        "change_entropy": 0.0,
        "change_entropy_pct": 0.0,
    }


def _parse_per_file_log(
    repo: Any,
    file_path: str,
    *,
    commit_limit: int,
    follow_renames: bool,
    provenance_classifier: Any | None = None,
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
        f"--format={_LOG_FORMAT}",
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

    # Split on the NUL record separator rather than newlines so multi-line
    # commit bodies (``%b``) don't get mistaken for numstat rows.
    commits: list[_CommitRec] = []
    for record in raw.split(_RECORD_SEP):
        if not record.strip():
            continue
        parsed = _parse_commit_record(record)
        if parsed is None:
            continue
        header, numstat_lines = parsed
        current = _CommitRec(
            sha=header["sha"],
            author_name=header["author_name"],
            author_email=header["author_email"],
            ts=header["ts"],
            is_merge=header["is_merge"],
            subject=header["subject"],
            body=header["body"],
        )
        if provenance_classifier is not None:
            prov = provenance_classifier.classify(
                header["author_name"],
                header["author_email"],
                header["committer_name"],
                header["committer_email"],
                f"{header['subject']}\n{header['body']}",
            )
            current.agent = prov.agent
            current.agent_tier = prov.autonomy_tier
        commits.append(current)
        for line in numstat_lines:
            numstat_parts = line.split("\t")
            if len(numstat_parts) < 3:
                continue
            stat_path = numstat_parts[2]
            match_path = stat_path
            if "=>" in stat_path:
                _old, _new = _extract_rename_paths(stat_path, known_paths)
                match_path = _new or stat_path
            if match_path in known_paths or match_path == file_path:
                try:
                    current.added += int(numstat_parts[0]) if numstat_parts[0] != "-" else 0
                    current.deleted += int(numstat_parts[1]) if numstat_parts[1] != "-" else 0
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
    as_of_ts: float | None = None,
    provenance_classifier: Any | None = None,
) -> dict:
    """Index a single file's git history. Runs in executor.

    When *precomputed_commits* is provided (the default when called from the
    batched full-repo path), the per-file ``git log`` subprocess is skipped
    entirely — eliminating the dominant process-spawn cost on large repos.

    *include_blame* gates the FULL-tier ``git blame`` ownership pass; the
    ESSENTIAL tier sets it False and falls back to commit-author ownership.

    *as_of_ts* anchors the recency windows (90d/30d, age, temporal decay) to a
    fixed reference time — the timestamp of the repo's most recent commit,
    supplied by the orchestrator. Anchoring to the repo's own HEAD rather than
    wall-clock ``now()`` makes indexing **deterministic** (re-indexing the same
    commit later yields identical windows) and **correct for historical
    checkouts** (scoring a worktree at an old commit measures the 90 days before
    *that* commit, not an empty window 6 months in its future). Falls back to
    ``now()`` when not supplied.
    """
    now = datetime.fromtimestamp(as_of_ts, tz=UTC) if as_of_ts is not None else datetime.now(UTC)
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
            provenance_classifier=provenance_classifier,
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
        # Per-author commit timestamps so a contributor's "last touched" reflects
        # *their own* last commit to this file — not the file's last commit by
        # anyone (which would credit you whenever a co-owner touches a shared
        # file). Consumed downstream by the owner-profile aggregator.
        author_last_ts: dict[str, int] = {}
        author_first_ts: dict[str, int] = {}

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
            if c.author_email:
                # Group by name, but pick a stable email per person: fold GitHub
                # noreply variants together and prefer a real address over a
                # noreply one, so a contributor who committed both ways doesn't
                # split into two buckets downstream (owner_profile keys on email).
                canon = canonicalize_author_email(c.author_email) or c.author_email
                existing = author_emails.get(c.author_name)
                if existing is None or (
                    existing.endswith("@users.noreply.github.com")
                    and not canon.endswith("@users.noreply.github.com")
                ):
                    author_emails[c.author_name] = canon
            if c.ts > 0:
                prev_last = author_last_ts.get(c.author_name)
                if prev_last is None or c.ts > prev_last:
                    author_last_ts[c.author_name] = c.ts
                prev_first = author_first_ts.get(c.author_name)
                if prev_first is None or c.ts < prev_first:
                    author_first_ts[c.author_name] = c.ts

        c90 = meta["commit_count_90d"]
        total_churn = meta["lines_added_90d"] + meta["lines_deleted_90d"]
        meta["avg_commit_size"] = total_churn / c90 if c90 > 0 else 0.0

        # Agent-provenance rollup: share of this file's indexed commits that
        # are agent-attributed, plus the per-autonomy-tier breakdown. The
        # per-commit labels were classified once during the walk; this is a
        # pure count over the already-carried records.
        agent_commits = [c for c in commits if getattr(c, "agent", None)]
        meta["agent_commit_count"] = len(agent_commits)
        meta["agent_authored_pct"] = len(agent_commits) / len(commits)
        tier_counts: Counter[str] = Counter(
            str(c.agent_tier) for c in agent_commits if c.agent_tier
        )
        meta["agent_tier_counts_json"] = json.dumps(dict(tier_counts))

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
            entry: dict[str, Any] = {
                "name": name,
                "email": author_emails.get(name, ""),
                "commit_count": count,
            }
            if name in author_last_ts:
                entry["last_commit_ts"] = author_last_ts[name]
            if name in author_first_ts:
                entry["first_commit_ts"] = author_first_ts[name]
            top_authors.append(entry)
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

        # Blame ownership + per-line index (FULL tier only). One
        # ``git blame --line-porcelain`` invocation produces both the
        # primary-owner signal and the in-memory ``BlameIndex`` consumed by
        # ``function_hotspot`` / ``code_age_volatility``. Skipped for large
        # files — git blame is O(lines) and can block the executor thread.
        #
        # Files below the function-blame commit floor used to take a second
        # blame pass through gitpython's ``repo.blame`` just for ownership.
        # That object-building parse is pure Python, holds the GIL across the
        # 20-way thread fan-out, and on a large repo MOST files sit below the
        # floor, so it dominated the FULL git phase. The porcelain parse now
        # serves ownership for every file; the floor only gates whether the
        # BlameIndex is retained for the function biomarkers, as before.
        if include_blame:
            try:
                file_size = (repo_path / file_path).stat().st_size
                if file_size <= _MAX_BLAME_SIZE_BYTES:
                    blame_idx = build_blame_index(
                        repo,
                        file_path,
                        repo_path=repo_path,
                    )
                    if blame_idx.lines:
                        if meta["commit_count_total"] >= _MIN_COMMITS_FOR_BLAME:
                            meta["blame_index"] = blame_idx
                        blame_name, blame_email, blame_pct = ownership_from_blame(blame_idx)
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
                    "date": datetime.fromtimestamp(c.ts, tz=UTC).isoformat() if c.ts else "",
                    "message": msg,
                    "author": c.author_name,
                }
                pr_match = _PR_NUMBER_RE.search(msg)
                if pr_match:
                    pr_num = pr_match.group(1) or pr_match.group(2) or pr_match.group(3)
                    entry["pr_number"] = int(pr_num)
                # Retain the commit body (byte-capped) only for significant
                # commits whose body shows decision intent — squash-merge repos
                # carry the full rationale here, which the PR/squash miner
                # consumes. The decision gate + cap keep the per-file JSON from
                # ballooning (the body is duplicated across every touched file).
                raw_body = getattr(c, "body", "") or ""
                if _body_carries_decision(c.subject, raw_body):
                    body = _truncate_body(raw_body)
                    if body:
                        entry["body"] = body
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

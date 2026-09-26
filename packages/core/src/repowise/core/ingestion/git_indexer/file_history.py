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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from ._constants import (
    _COMMIT_CATEGORIES,
    _DECISION_SIGNAL_WORDS,
    _MAX_BLAME_SIZE_BYTES,
    _MAX_SIGNIFICANT_COMMITS,
    _MAX_TOP_AUTHORS,
    _PR_BODY_MARKERS,
    _PR_NUMBER_RE,
    HOTSPOT_HALFLIFE_DAYS,
    _truncate_body,
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


logger = structlog.get_logger(__name__)

__all__ = ["DECAY_REFRESH_KEYS", "index_file", "new_meta"]

# The anchor-dependent window/decay fields — the only git-metadata columns that
# *recover* as the repo's newest-commit anchor advances (see issue #728). An
# incremental update recomputes just these for idle (unchanged) files off the
# repo-wide walk and persists a decay-only partial row, leaving ownership / age
# / authorship (which need full history and are only correct from the init
# walk) untouched. ``co_change_partners_json`` / ``co_change_partner_count`` /
# ``co_change_mass`` / ``change_entropy`` /
# ``prior_defect_*`` are merged onto the metadata after the per-file pass, so
# they refresh together with the window churn fields ``index_file`` computes.
DECAY_REFRESH_KEYS = (
    "commit_count_90d",
    "commit_count_30d",
    "lines_added_90d",
    "lines_deleted_90d",
    "merge_commit_count_90d",
    "temporal_hotspot_score",
    "prior_defect_count",
    "prior_defect_raw_count",
    "change_entropy",
    "co_change_partners_json",
    "co_change_partner_count",
    "co_change_mass",
)


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
        # prior-defects baseline definition (product == benchmark). The raw
        # variant is the same walk before fix-shape filtering (fix_shape.py).
        "prior_defect_count": 0,
        "prior_defect_raw_count": 0,
        # Repo-relative rank of prior_defect_count; see enrich.compute_percentiles.
        "prior_defect_pct": 0.0,
        # Agent provenance rollup: how much of this file's indexed history is
        # agent-attributed (local channels only — see agent_provenance module).
        # agent_authored_pct stays None when the file has no commits at all.
        "agent_commit_count": 0,
        "agent_authored_pct": None,
        "agent_tier_counts_json": "{}",
        # Line-level agent share (agent-trace ranges[]): distinct AI-written
        # lines + {model_id: line_count}. Merged in by the orchestrator from the
        # repo-wide trace index (path-keyed, like co-change/prior-defects), so
        # these stay at the default unless the repo ships .agent-trace/.
        "agent_line_count": 0,
        "agent_line_model_json": "{}",
        # Temporal hotspot score (exponentially decayed churn)
        "temporal_hotspot_score": 0.0,
        # Change entropy (Hassan HCM) — populated repo-wide by the co-change
        # walk, percentile by enrich.compute_percentiles. Default 0.0 leaves
        # the signal silent on the ESSENTIAL tier / files that never co-changed.
        "change_entropy": 0.0,
        "change_entropy_pct": 0.0,
        # Co-change breadth over every partner the repo-wide walk found, not
        # the length of the truncated partner list. Defaults leave
        # ``co_change_scatter`` silent when the walk did not run.
        "co_change_partner_count": 0,
        "co_change_mass": 0.0,
        "co_change_scatter_pct": 0.0,
    }


def _parse_per_file_log(
    repo: Any,
    file_path: str,
    *,
    commit_limit: int,
    follow_renames: bool,
    provenance_classifier: Any | None = None,
    note_agents: dict[str, str] | None = None,
    trace_index: Any | None = None,
) -> tuple[list[_CommitRec] | None, str | None]:
    """Run a per-file ``git log --numstat`` and parse it into commit records.

    Returns ``(commits, original_path)``. Only used when no precomputed commit
    index is available (i.e. when rename-tracking is on).
    """
    try:
        raw = repo.git.log(*_per_file_log_args(file_path, commit_limit, follow_renames))
    except Exception:
        return None, None

    if not raw.strip():
        return [], None

    known_paths, orig_path = _known_paths(repo, file_path, commit_limit, follow_renames)
    labeler = (
        _CommitLabeler(provenance_classifier, note_agents, trace_index)
        if provenance_classifier is not None
        else None
    )
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
        commits.append(current)
        changed_paths = _add_own_churn(current, numstat_lines, known_paths)
        if labeler is not None:
            prov = labeler.label(header, changed_paths)
            current.agent = prov.agent
            current.agent_tier = prov.autonomy_tier
    return commits, orig_path


def _per_file_log_args(file_path: str, commit_limit: int, follow_renames: bool) -> list[str]:
    walk = [f"-{commit_limit}", "--numstat", f"--format={_LOG_FORMAT}", "--", file_path]
    if follow_renames:
        return ["--follow", *walk]
    # Match the recent/deep repo-wide lanes. Without one shared non-merge
    # contract, a file moving between fallback and shared sampling could
    # lose a retained merge even while ``commit_limit`` increased.
    # A pathspec normally restricts numstat to this file, which represents
    # a rename differently from the repo-wide lanes. Full-diff keeps commit
    # selection per-file but makes churn and changed-path provenance use
    # the same complete diff as the recent/deep walks.
    return ["--no-merges", "--full-diff", *walk]


def _known_paths(
    repo: Any, file_path: str, commit_limit: int, follow_renames: bool
) -> tuple[set[str], str | None]:
    """The names this file's churn is matched under, and its original path under ``--follow``."""
    known_paths: set[str] = {file_path}
    if not follow_renames:
        return known_paths, None
    orig_path = detect_original_path(repo, file_path, commit_limit)
    if orig_path:
        known_paths.add(orig_path)
    return known_paths, orig_path


def _add_own_churn(
    current: _CommitRec, numstat_lines: list[str], known_paths: set[str]
) -> set[str]:
    """Add the churn of rows naming this file to *current*; return every changed path.

    A rename row adds both of its names to *known_paths*.
    """
    changed_paths: set[str] = set()
    for line in numstat_lines:
        numstat_parts = line.split("\t")
        if len(numstat_parts) < 3:
            continue
        match_path = numstat_parts[2]
        if "=>" in match_path:
            _old, _new = _extract_rename_paths(match_path, known_paths)
            match_path = _new or match_path
        changed_paths.add(match_path)
        if match_path in known_paths:
            _add_row_churn(current, numstat_parts)
    return changed_paths


def _add_row_churn(current: _CommitRec, numstat_parts: list[str]) -> None:
    try:
        current.added += int(numstat_parts[0]) if numstat_parts[0] != "-" else 0
        current.deleted += int(numstat_parts[1]) if numstat_parts[1] != "-" else 0
    except ValueError:
        pass


class _CommitLabeler:
    """Labels a commit's agent provenance from its header and changed paths."""

    def __init__(
        self, classifier: Any, note_agents: dict[str, str] | None, trace_index: Any | None
    ) -> None:
        self._classifier = classifier
        self._note_agents = note_agents or {}
        self._trace_index = trace_index

    def label(self, header: dict, changed_paths: set[str]) -> Any:
        trace_hit = (
            self._trace_index.resolve(header["sha"], header["parents"], changed_paths)
            if self._trace_index
            else None
        )
        return self._classifier.classify(
            header["author_name"],
            header["author_email"],
            header["committer_name"],
            header["committer_email"],
            f"{header['subject']}\n{header['body']}",
            note_agent=self._note_agents.get(header["sha"]),
            trace_agent=trace_hit[0] if trace_hit else None,
            trace_confidence=trace_hit[1] if trace_hit else "high",
        )


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
    note_agents: dict[str, str] | None = None,
    trace_index: Any | None = None,
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
    now = _window_anchor(as_of_ts)
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
            note_agents=note_agents,
            trace_index=trace_index,
        )
        if commits is None:
            return {"file_path": file_path}

    if not commits:
        return meta

    meta["commit_count_total"] = len(commits)
    meta["commit_count_capped"] = len(commits) >= commit_limit

    # Each step writes its fields in turn, so a failure keeps the fields
    # the earlier steps already wrote.
    try:
        _add_span(meta, commits, now)
        authors = _Authors.tally(commits, ninety_days_ago_ts)
        _add_windows(meta, commits, ninety_days_ago_ts, thirty_days_ago_ts)
        _add_agent_rollup(meta, commits)
        meta["temporal_hotspot_score"] = _temporal_hotspot_score(commits, now)
        _add_ownership(meta, authors)
        if include_blame:
            _add_blame_ownership(meta, repo, repo_path, now)
        _add_commit_messages(meta, commits)
        # Only the per-file ``--follow`` walk reports an original path.
        if orig_path:
            meta["original_path"] = orig_path
        meta["is_stable"] = _is_stable(meta)
    except Exception:
        logger.debug("git_indexer_partial_failure", file_path=file_path, exc_info=True)

    return meta


def _window_anchor(as_of_ts: float | None) -> datetime:
    if as_of_ts is not None:
        return datetime.fromtimestamp(as_of_ts, tz=UTC)
    return datetime.now(UTC)


def _add_span(meta: dict[str, Any], commits: list[_CommitRec], now: datetime) -> None:
    timestamps = [c.ts for c in commits if c.ts > 0]
    if timestamps:
        first_ts = min(timestamps)
        last_ts = max(timestamps)
        meta["first_commit_at"] = datetime.fromtimestamp(first_ts, tz=UTC)
        meta["last_commit_at"] = datetime.fromtimestamp(last_ts, tz=UTC)
        meta["age_days"] = (now - datetime.fromtimestamp(first_ts, tz=UTC)).days


def _add_windows(
    meta: dict[str, Any],
    commits: list[_CommitRec],
    ninety_days_ago_ts: float,
    thirty_days_ago_ts: float,
) -> None:
    """Commit counts and churn over the trailing 90 and 30 days."""
    for c in commits:
        if c.ts >= ninety_days_ago_ts:
            meta["commit_count_90d"] += 1
            meta["lines_added_90d"] += c.added
            meta["lines_deleted_90d"] += c.deleted
            if c.is_merge:
                meta["merge_commit_count_90d"] += 1
        if c.ts >= thirty_days_ago_ts:
            meta["commit_count_30d"] += 1

    c90 = meta["commit_count_90d"]
    total_churn = meta["lines_added_90d"] + meta["lines_deleted_90d"]
    meta["avg_commit_size"] = total_churn / c90 if c90 > 0 else 0.0


_NOREPLY_DOMAIN = "@users.noreply.github.com"


@dataclass
class _Authors:
    """Per-author commit counts, one stable email each, and first/last commit times."""

    counts: Counter[str] = field(default_factory=Counter)
    recent_counts: Counter[str] = field(default_factory=Counter)
    emails: dict[str, str] = field(default_factory=dict)
    # Each author's own first/last commit to this file, not the file's: a
    # co-owner touching a shared file must not refresh everyone's "last touched".
    first_ts: dict[str, int] = field(default_factory=dict)
    last_ts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def tally(cls, commits: list[_CommitRec], recent_since_ts: float) -> _Authors:
        authors = cls()
        for c in commits:
            name = c.author_name
            authors.counts[name] += 1
            if c.ts >= recent_since_ts:
                authors.recent_counts[name] += 1
            if c.author_email:
                authors._prefer_email(name, c.author_email)
            if c.ts > 0:
                authors.last_ts[name] = max(authors.last_ts.get(name, c.ts), c.ts)
                authors.first_ts[name] = min(authors.first_ts.get(name, c.ts), c.ts)
        return authors

    def _prefer_email(self, name: str, email: str) -> None:
        # Group by name, but pick a stable email per person: fold GitHub
        # noreply variants together and prefer a real address over a
        # noreply one, so a contributor who committed both ways doesn't
        # split into two buckets downstream (owner_profile keys on email).
        canon = canonicalize_author_email(email) or email
        existing = self.emails.get(name)
        if existing is None or (
            existing.endswith(_NOREPLY_DOMAIN) and not canon.endswith(_NOREPLY_DOMAIN)
        ):
            self.emails[name] = canon

    def entry(self, name: str, count: int) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": name,
            "email": self.emails.get(name, ""),
            "commit_count": count,
        }
        if name in self.last_ts:
            entry["last_commit_ts"] = self.last_ts[name]
        if name in self.first_ts:
            entry["first_commit_ts"] = self.first_ts[name]
        return entry


def _add_agent_rollup(meta: dict[str, Any], commits: list[_CommitRec]) -> None:
    """Share of the file's commits that are agent-attributed, and the per-tier breakdown."""
    agent_commits = [c for c in commits if getattr(c, "agent", None)]
    meta["agent_commit_count"] = len(agent_commits)
    meta["agent_authored_pct"] = len(agent_commits) / len(commits)
    tier_counts: Counter[str] = Counter(str(c.agent_tier) for c in agent_commits if c.agent_tier)
    meta["agent_tier_counts_json"] = json.dumps(dict(tier_counts))


def _temporal_hotspot_score(commits: list[_CommitRec], now: datetime) -> float:
    """Exponentially decayed per-commit churn."""
    _ln2 = math.log(2)
    temporal_score = 0.0
    for c in commits:
        age_days = max((now.timestamp() - c.ts) / 86400.0, 0.0)
        weight = math.exp(-_ln2 * age_days / HOTSPOT_HALFLIFE_DAYS)
        lines = min((c.added + c.deleted) / 100.0, 3.0)
        temporal_score += weight * lines
    return temporal_score


def _add_ownership(meta: dict[str, Any], authors: _Authors) -> None:
    """Contributor count, bus factor, top authors and the overall and 90-day owners."""
    meta["contributor_count"] = len(authors.counts)
    total_commits = sum(authors.counts.values())
    if total_commits > 0:
        meta["bus_factor"] = _bus_factor(authors.counts, total_commits)

    top_authors = [
        authors.entry(name, count) for name, count in authors.counts.most_common(_MAX_TOP_AUTHORS)
    ]
    meta["top_authors_json"] = json.dumps(top_authors)

    if top_authors:
        primary = top_authors[0]
        meta["primary_owner_name"] = primary["name"]
        meta["primary_owner_email"] = primary["email"]
        meta["primary_owner_commit_pct"] = (
            primary["commit_count"] / total_commits if total_commits > 0 else 0.0
        )

    if authors.recent_counts:
        recent_top = authors.recent_counts.most_common(1)[0]
        meta["recent_owner_name"] = recent_top[0]
        recent_total = sum(authors.recent_counts.values())
        meta["recent_owner_commit_pct"] = recent_top[1] / recent_total if recent_total > 0 else 0.0


def _bus_factor(author_counts: Counter[str], total_commits: int) -> int:
    """How many top authors it takes to cover 80% of the commits."""
    threshold = total_commits * 0.8
    running = 0
    bus = 0
    for _name, cnt in author_counts.most_common():
        running += cnt
        bus += 1
        if running >= threshold:
            break
    return bus


def _add_blame_ownership(meta: dict[str, Any], repo: Any, repo_path: Path, now: datetime) -> None:
    """Blame ownership plus the per-line ``BlameIndex`` (FULL tier), best effort.

    One ``git blame --line-porcelain`` pass serves both the primary-owner
    signal and the index ``function_hotspot`` / ``code_age_volatility`` read.
    Files over the size cap are skipped: blame is O(lines) and can block the
    executor thread. The commit floor only gates whether the index is kept.
    """
    file_path = meta["file_path"]
    try:
        if (repo_path / file_path).stat().st_size > _MAX_BLAME_SIZE_BYTES:
            return
        blame_idx = build_blame_index(repo, file_path, repo_path=repo_path)
        if not blame_idx.lines:
            return
        blame_idx.as_of_ts = int(now.timestamp())
        if meta["commit_count_total"] >= _MIN_COMMITS_FOR_BLAME:
            meta["blame_index"] = blame_idx
        blame_name, blame_email, blame_pct = ownership_from_blame(blame_idx)
        if blame_name:
            meta["primary_owner_name"] = blame_name
            meta["primary_owner_email"] = blame_email
            meta["primary_owner_commit_pct"] = blame_pct
    except Exception:
        pass  # blame is best-effort


def _add_commit_messages(meta: dict[str, Any], commits: list[_CommitRec]) -> None:
    """Significant commits (with PR numbers and decision bodies) and category counts."""
    sig_commits: list[dict[str, Any]] = []
    for c in commits:
        msg = c.subject[:200]
        if is_significant_commit(msg, c.author_name):
            sig_commits.append(_significant_entry(c, msg))
            if len(sig_commits) >= _MAX_SIGNIFICANT_COMMITS:
                break
    # Every commit is classified, so the category ratios cover the whole history.
    category_counts: Counter[str] = Counter(
        cat for c in commits if (cat := _commit_category(c.subject[:200])) is not None
    )
    meta["significant_commits_json"] = json.dumps(sig_commits)
    meta["commit_categories_json"] = json.dumps(dict(category_counts))


def _significant_entry(c: _CommitRec, msg: str) -> dict[str, Any]:
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
    return entry


def _commit_category(msg: str) -> str | None:
    for cat, pattern in _COMMIT_CATEGORIES.items():
        if pattern.search(msg):
            return cat
    return None


def _is_stable(meta: dict[str, Any]) -> bool:
    """A long history with nothing in the last 90 days."""
    return meta["commit_count_total"] > 10 and meta["commit_count_90d"] == 0

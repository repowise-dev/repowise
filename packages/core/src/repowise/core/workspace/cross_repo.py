"""Cross-repo intelligence: co-change detection, manifest edges, overlay persistence.

Manifest scanning itself lives in :mod:`.manifests`; this module runs it and
persists what it finds alongside the co-change edges.

Runs during ``repowise update --workspace`` (write path).  The resulting JSON
is loaded by :class:`CrossRepoEnricher` in the MCP server (read path).

No new DB tables — all cross-repo data lives in
``.repowise-workspace/cross_repo_edges.json``.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import math
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..co_change import CO_CHANGE_DECAY_TAU
from ..fsutils import atomic_write_text
from .config import WorkspaceConfig, ensure_workspace_data_dir
from .manifests import (
    CrossRepoPackageDep,
    CrossRepoPackageDiagnostic,
    detect_package_dependencies_with_diagnostics,
)

_log = logging.getLogger("repowise.workspace.cross_repo")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CROSS_REPO_EDGES_FILENAME = "cross_repo_edges.json"

# Bump when the meaning of the persisted fields changes; ``load_overlay``
# discards older versions so stale scores never reach consumers that assume
# the new semantics (v2: strength became a bounded [0,1) session share;
# v3: co-change rows gained bounded supporting evidence — authors, example
# commit pairs, max time gap — issue #483).
_OVERLAY_VERSION: int = 3

_DEFAULT_TIME_WINDOW_HOURS: int = 24
_DEFAULT_COMMIT_LIMIT: int = 500
# Strength is a bounded [0, 1) share (see detect_cross_repo_co_changes), so
# the floor is a ratio: pairs that co-occur in under ~20% of the less-active
# file's sessions are coincidence, not coupling.
_MIN_CROSS_REPO_SCORE: float = 0.2
# A pair must co-occur in at least this many distinct work sessions. A single
# session proves nothing — one afternoon of unrelated commits in two repos
# would otherwise mint 100%-strength edges.
_MIN_CO_SESSIONS: int = 2
# Files changed in more than this share of a repo's scanned commits are
# ambient (progress diaries, changelogs, version stamps): they ride along
# with everything and carry no pairing signal. 0.20 leaves headroom above
# genuinely hot files (observed maxima sit near 15% in active repos), and
# the filter is skipped entirely for short histories where shares are noise.
_UBIQUITY_MAX_COMMIT_SHARE: float = 0.20
_UBIQUITY_MIN_HISTORY: int = 30
# Laplace-style smoothing added to the strength denominator so a pair seen in
# 2-of-2 sessions (67% after smoothing) cannot outrank sustained coupling
# like 30-of-40 (73%).
_STRENGTH_SMOOTHING: float = 1.0
# Public: the workspace API reports which of these caps trimmed the overlay.
MAX_EDGES: int = 200
# One hyperactive repo pair must not consume the whole edge budget and starve
# the other pairs out of the overlay.
MAX_EDGES_PER_REPO_PAIR: int = 50
# Per-session cap on files paired per side, guarding the O(N*M) cross-product
# against sprawling release/codemod sessions.
_MAX_FILES_PER_SESSION_SIDE: int = 20
# Evidence capture (#483): how many example matched commit pairs survive per
# co-change pair, and how many distinct authors are recorded. Bounded so a
# pair backed by hundreds of commit pairs cannot bloat the persisted overlay.
_MAX_EVIDENCE_COMMIT_PAIRS: int = 3
_MAX_EVIDENCE_AUTHORS: int = 5

# ---------------------------------------------------------------------------
# Noise-file filtering
#
# Cross-repo co-change is a purely temporal, same-author signal. Files that are
# touched as a side effect of unrelated work (CI configs, lockfiles, generated
# code, localization blobs, binary/asset metadata) pollute the top results with
# pairs that have no real relationship. Unlike intra-repo co-change, this path
# runs on raw ``git log --name-only`` output, so we filter these out explicitly
# before pairing. See issue #475.
# ---------------------------------------------------------------------------

# Glob patterns matched against the basename, or (when they contain "/") against
# the full posix path and any "*/"-suffixed subpath.
_NOISE_FILE_PATTERNS: tuple[str, ...] = (
    # CI / workflow
    ".github/workflows/*",
    ".gitlab-ci.yml",
    ".circleci/*",
    "azure-pipelines.yml",
    "Jenkinsfile",
    # Lockfiles
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
    # Generated / minified / build output
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.pb.go",
    "*_pb2.py",
    "*_pb2_grpc.py",
    # Localization resource files
    "*.po",
    "*.mo",
    # Release ritual: changelogs ride along with every release session and
    # pair with whatever else shipped that day
    "CHANGELOG*",
    "changelog*",
    # Binary / asset metadata (Unity and similar engines)
    "*.prefab",
    "*.meta",
    "*.asset",
)

# Path segments (case-insensitive) that mark a file as generated/vendored/noise
# wherever they appear in the path.
_NOISE_DIR_SEGMENTS: frozenset[str] = frozenset(
    {
        "node_modules",
        "dist",
        "build",
        "vendor",
        "generated",
        "__generated__",
        "locales",
        "locale",
        "localization",
        "i18n",
        "translations",
    }
)


def _is_noise_path(path: str) -> bool:
    """Return True if *path* is a non-signal file that should not be paired."""
    p = PurePosixPath(path.replace("\\", "/"))
    if {seg.lower() for seg in p.parts} & _NOISE_DIR_SEGMENTS:
        return True
    posix = p.as_posix()
    name = p.name
    for pat in _NOISE_FILE_PATTERNS:
        if "/" in pat:
            if fnmatch.fnmatch(posix, pat) or fnmatch.fnmatch(posix, "*/" + pat):
                return True
        elif fnmatch.fnmatch(name, pat):
            return True
    return False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CoChangeEvidence:
    """Bounded supporting evidence for one cross-repo co-change pair (#483).

    A pair is a temporal, same-author hint; the evidence is what lets a user
    judge whether the relationship is meaningful. The sample is capped —
    ``_MAX_EVIDENCE_COMMIT_PAIRS`` example matched commit pairs per pair, so
    the overlay cannot bloat on pairs backed by hundreds of commit pairs.
    """

    authors: list[str]  # distinct author emails, dedup, insertion order
    commit_pairs: list[dict]  # capped [{source_sha, target_sha, date, gap_hours}]
    max_gap_hours: float  # largest time gap between a matched commit pair


@dataclass
class CrossRepoCoChange:
    source_repo: str
    source_file: str
    target_repo: str
    target_file: str
    strength: float  # bounded [0, 1) recency-weighted share of co-sessions
    frequency: int  # distinct work sessions in which both files changed
    last_date: str  # ISO date of most recent co-change
    evidence: CoChangeEvidence | None = None  # bounded sample, None pre-#483 overlays


@dataclass
class CrossRepoOverlay:
    version: int = _OVERLAY_VERSION
    generated_at: str = ""
    co_changes: list[CrossRepoCoChange] = field(default_factory=list)
    package_deps: list[CrossRepoPackageDep] = field(default_factory=list)
    package_diagnostics: list[CrossRepoPackageDiagnostic] = field(default_factory=list)
    repo_summaries: dict[str, dict] = field(default_factory=dict)
    #: How many pairs cleared the strength/session thresholds before
    #: ``MAX_EDGES`` / ``MAX_EDGES_PER_REPO_PAIR`` trimmed ``co_changes``.
    #: Equal to ``len(co_changes)`` when nothing was dropped. Not a count of
    #: every pair in git history: ``_MAX_FILES_PER_SESSION_SIDE`` bounds each
    #: session before pairing, so pairs involving an evicted file are in
    #: neither number.
    total_co_changes: int = 0
    total_package_diagnostics: int = 0

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "co_changes": [asdict(c) for c in self.co_changes],
            "package_deps": [asdict(d) for d in self.package_deps],
            "package_diagnostics": [asdict(d) for d in self.package_diagnostics],
            "repo_summaries": self.repo_summaries,
            "total_co_changes": self.total_co_changes or len(self.co_changes),
            "total_package_diagnostics": self.total_package_diagnostics
            or len(self.package_diagnostics),
        }

    @classmethod
    def from_dict(cls, data: dict) -> CrossRepoOverlay:
        def _co_change(c: dict) -> CrossRepoCoChange:
            ev = c.get("evidence")
            evidence = None
            if isinstance(ev, dict) and (ev.get("commit_pairs") or ev.get("authors")):
                evidence = CoChangeEvidence(
                    authors=list(ev.get("authors") or []),
                    commit_pairs=list(ev.get("commit_pairs") or []),
                    max_gap_hours=float(ev.get("max_gap_hours", 0.0)),
                )
            return CrossRepoCoChange(
                source_repo=c["source_repo"],
                source_file=c["source_file"],
                target_repo=c["target_repo"],
                target_file=c["target_file"],
                strength=float(c["strength"]),
                frequency=int(c["frequency"]),
                last_date=c.get("last_date", ""),
                evidence=evidence,
            )

        co_changes = [_co_change(c) for c in data.get("co_changes", [])]
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            co_changes=co_changes,
            package_deps=[CrossRepoPackageDep(**d) for d in data.get("package_deps", [])],
            package_diagnostics=[
                CrossRepoPackageDiagnostic(**d) for d in data.get("package_diagnostics", [])
            ],
            repo_summaries=data.get("repo_summaries", {}),
            total_co_changes=data.get("total_co_changes", len(co_changes)),
            total_package_diagnostics=data.get(
                "total_package_diagnostics",
                len(data.get("package_diagnostics", [])),
            ),
        )


# ---------------------------------------------------------------------------
# Git log mining
# ---------------------------------------------------------------------------


@dataclass
class _GitCommit:
    author_email: str
    timestamp: int  # Unix epoch
    files: list[str] = field(default_factory=list)
    author_name: str = ""
    sha: str = ""  # full commit SHA (%H)

    @property
    def author_identity(self) -> str:
        """Best-effort cross-repo identity for this author.

        The same person routinely commits with different emails per clone
        (work vs personal vs GitHub noreply), which would make their own
        repos invisible to each other under email-keyed matching. A
        normalized author name ("Raghav Chamadiya" == "RaghavChamadiya")
        bridges those configs; the email remains the fallback.
        """
        normalized = re.sub(r"[^a-z0-9]", "", self.author_name.lower())
        return normalized or self.author_email.lower()


def _parse_git_log(
    repo_path: Path,
    commit_limit: int = _DEFAULT_COMMIT_LIMIT,
) -> list[_GitCommit]:
    """Run ``git log`` and parse into structured commit records.

    Returns list of commits with author email, timestamp, and changed files.
    Uses subprocess.run — same pattern as ``update.py:get_head_commit()``.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"-{commit_limit}",
                # %x01 separator: author names may contain "|"
                "--format=%x00%ae%x01%an%x01%ct%x01%H",
                "--name-only",
                "--no-merges",
            ],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
        )
        if result.returncode != 0:
            _log.debug("git log failed for %s: %s", repo_path, result.stderr)
            return []
    except Exception:
        _log.debug("git log subprocess failed for %s", repo_path, exc_info=True)
        return []

    commits: list[_GitCommit] = []
    current: _GitCommit | None = None

    for line in result.stdout.splitlines():
        if line.startswith("\x00") or line == "\x00":
            # Commit boundary — flush previous, parse header
            if current is not None and current.files:
                commits.append(current)

            header = line.lstrip("\x00").strip()
            parts = header.split("\x01", 3)
            if len(parts) >= 3:
                email = parts[0].strip()
                name = parts[1].strip()
                try:
                    ts = int(parts[2].strip())
                except (ValueError, TypeError):
                    ts = 0
                sha = parts[3].strip() if len(parts) == 4 else ""
                current = _GitCommit(
                    author_email=email,
                    timestamp=ts,
                    author_name=name,
                    sha=sha,
                )
            else:
                current = None
        elif current is not None:
            path = line.strip()
            if path:
                current.files.append(path)

    # Flush last commit
    if current is not None and current.files:
        commits.append(current)

    return commits


# ---------------------------------------------------------------------------
# Cross-repo co-change detection
# ---------------------------------------------------------------------------


def _drop_ubiquitous_files(
    repo_commits: dict[str, list[_GitCommit]],
) -> dict[str, list[_GitCommit]]:
    """Remove files that appear in an outsized share of a repo's commits.

    A progress diary or changelog edited alongside nearly every commit rides
    into every temporal pairing and, before this filter existed, produced the
    top cross-repo "couplings" in real workspaces. Ubiquity is measured per
    repo over the scanned window, so the filter adapts to any repo's habits
    without a hardcoded filename list.
    """
    filtered: dict[str, list[_GitCommit]] = {}
    for alias, commits in repo_commits.items():
        if len(commits) < _UBIQUITY_MIN_HISTORY:
            filtered[alias] = commits
            continue
        file_counts: dict[str, int] = defaultdict(int)
        for c in commits:
            for f in c.files:
                file_counts[f] += 1
        threshold = _UBIQUITY_MAX_COMMIT_SHARE * len(commits)
        ubiquitous = {f for f, n in file_counts.items() if n > threshold}
        kept: list[_GitCommit] = []
        for c in commits:
            c.files = [f for f in c.files if f not in ubiquitous]
            if c.files:
                kept.append(c)
        if kept:
            filtered[alias] = kept
    return filtered


def _build_sessions(
    tagged_commits: list[tuple[str, _GitCommit]],
    window_seconds: int,
) -> list[list[tuple[str, _GitCommit]]]:
    """Chain one author's commits (sorted by timestamp) into work sessions.

    A new session starts when the gap to the previous commit exceeds the
    window, or when the session's total span would exceed it: without the
    span cap, an author who commits every day never has a >24h gap and
    months of history would chain into one mega-session. Chaining (rather
    than pairing every commit against every other commit in range) is what
    keeps a busy day from counting quadratically: one session contributes
    exactly once per file pair.
    """
    sessions: list[list[tuple[str, _GitCommit]]] = []
    current: list[tuple[str, _GitCommit]] = []
    session_start_ts: int | None = None
    prev_ts: int | None = None
    for alias, commit in tagged_commits:
        if prev_ts is not None and (
            commit.timestamp - prev_ts > window_seconds
            or commit.timestamp - (session_start_ts or prev_ts) > window_seconds
        ):
            sessions.append(current)
            current = []
            session_start_ts = commit.timestamp
        elif session_start_ts is None:
            session_start_ts = commit.timestamp
        current.append((alias, commit))
        prev_ts = commit.timestamp
    if current:
        sessions.append(current)
    return sessions


def detect_cross_repo_co_changes(
    repo_paths: dict[str, Path],
    *,
    time_window_hours: int = _DEFAULT_TIME_WINDOW_HOURS,
    commit_limit: int = _DEFAULT_COMMIT_LIMIT,
    min_score: float = _MIN_CROSS_REPO_SCORE,
    min_sessions: int = _MIN_CO_SESSIONS,
) -> tuple[list[CrossRepoCoChange], int]:
    """Find files across repos that the same author changes in the same
    work sessions.

    Algorithm:
    1. Parse git logs for all repos
    2. Drop noise files (CI, lockfiles, generated, localization, assets),
       then files present in an outsized share of a repo's commits
    3. Chain each author's commits into work sessions (gap > window starts
       a new session); a session touching >=2 repos is a co-session
    4. Each co-session credits every cross-repo file pair once with the
       session's recency decay weight; every session (co- or not) credits
       each of its files' activity totals with the same weight
    5. strength = co-session weight / (less-active file's session weight
       + smoothing) — a bounded [0, 1) "share of the less-active file's
       recent work that also touched the partner"
    6. Keep pairs with >= min_sessions co-sessions and strength >= min_score,
       cap per repo pair, then globally

    Returns the capped list and the number of pairs that cleared step 6's
    thresholds before capping, so the caller can say how much it is showing.
    That total is measured after step 3's per-session file cap, so it bounds
    what this miner scored, not what git history contains.
    """
    if len(repo_paths) < 2:
        return [], 0

    now_ts = time.time()
    window_seconds = time_window_hours * 3600

    # Step 1: Parse git logs for all repos
    repo_commits: dict[str, list[_GitCommit]] = {}
    for alias, path in repo_paths.items():
        commits = _parse_git_log(path, commit_limit)
        if commits:
            repo_commits[alias] = commits

    # Step 2: Drop noise files, then commits left with nothing, then
    # ambient files that ride along with everything
    filtered: dict[str, list[_GitCommit]] = {}
    for alias, commits in repo_commits.items():
        kept: list[_GitCommit] = []
        for c in commits:
            c.files = [f for f in c.files if not _is_noise_path(f)]
            if c.files:
                kept.append(c)
        if kept:
            filtered[alias] = kept
    repo_commits = _drop_ubiquitous_files(filtered)

    if len(repo_commits) < 2:
        return [], 0

    # Step 3: Group all commits by author identity, tagged with repo alias
    author_commits: dict[str, list[tuple[str, _GitCommit]]] = defaultdict(list)
    for alias, commits in repo_commits.items():
        for c in commits:
            author_commits[c.author_identity].append((alias, c))

    # Step 4: Walk each author's sessions, accumulating per-file activity
    # (the strength denominator) and per-pair co-session weight/count.
    file_activity: dict[tuple[str, str], float] = defaultdict(float)
    pair_scores: dict[tuple[str, str, str, str], float] = defaultdict(float)
    pair_freq: dict[tuple[str, str, str, str], int] = defaultdict(int)
    pair_last_ts: dict[tuple[str, str, str, str], int] = {}
    # Evidence capture (#483): per-pair bounded sample of matched commits.
    pair_evidence: dict[tuple[str, str, str, str], dict] = defaultdict(
        lambda: {"authors": [], "commit_pairs": [], "max_gap_hours": 0.0}
    )

    for tagged_commits in author_commits.values():
        tagged_commits.sort(key=lambda x: x[1].timestamp)

        for session in _build_sessions(tagged_commits, window_seconds):
            last_ts = max(c.timestamp for _, c in session)
            age_days = max((now_ts - last_ts) / 86400.0, 0.0)
            weight = math.exp(-age_days / CO_CHANGE_DECAY_TAU)

            # Files per repo for this session, with how many of the
            # session's commits touched each one
            session_files: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for alias, commit in session:
                for f in commit.files:
                    session_files[alias][f] += 1

            # Every session counts toward its files' activity totals, so a
            # file's denominator reflects all its work — including sessions
            # confined to one repo. This is what bounds strength below 1.
            for alias, files in session_files.items():
                for f in files:
                    file_activity[(alias, f)] += weight

            if len(session_files) < 2:
                continue

            # Cap each side by session centrality (files touched by the most
            # commits in the session first): an alphabetical cut would
            # systematically evict late-sorting paths like src/** from busy
            # sessions while keeping __tests__/** and content/** noise.
            capped_files = {
                alias: [
                    f
                    for f, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[
                        :_MAX_FILES_PER_SESSION_SIDE
                    ]
                ]
                for alias, counts in session_files.items()
            }
            aliases = sorted(capped_files)
            for ai in range(len(aliases)):
                for bi in range(ai + 1, len(aliases)):
                    repo_a, repo_b = aliases[ai], aliases[bi]
                    files_a = capped_files[repo_a]
                    files_b = capped_files[repo_b]
                    for fa in files_a:
                        for fb in files_b:
                            key = (repo_a, fa, repo_b, fb)
                            pair_scores[key] += weight
                            pair_freq[key] += 1
                            if key not in pair_last_ts or last_ts > pair_last_ts[key]:
                                pair_last_ts[key] = last_ts
                            # Evidence (#483): one example matched commit pair
                            # per session — the commits in each repo that
                            # touched the two files — capped per pair and
                            # deduped by (source_sha, target_sha).
                            ev = pair_evidence[key]
                            src_commit = next(
                                (c for _, c in session if c.sha and fa in c.files),
                                None,
                            )
                            tgt_commit = next(
                                (c for _, c in session if c.sha and fb in c.files),
                                None,
                            )
                            if src_commit is not None and tgt_commit is not None:
                                gap = abs(src_commit.timestamp - tgt_commit.timestamp) / 3600.0
                                ev["max_gap_hours"] = max(ev["max_gap_hours"], gap)
                                for email in (src_commit.author_email, tgt_commit.author_email):
                                    if email and email not in ev["authors"]:
                                        ev["authors"].append(email)
                                pair = (src_commit.sha, tgt_commit.sha)
                                if len(ev["commit_pairs"]) < _MAX_EVIDENCE_COMMIT_PAIRS and not any(
                                    p["source_sha"] == pair[0] and p["target_sha"] == pair[1]
                                    for p in ev["commit_pairs"]
                                ):
                                    ev["commit_pairs"].append(
                                        {
                                            "source_sha": pair[0][:8],
                                            "target_sha": pair[1][:8],
                                            "date": datetime.fromtimestamp(
                                                last_ts, tz=UTC
                                            ).strftime("%Y-%m-%d"),
                                            "gap_hours": round(gap, 1),
                                        }
                                    )
                            ev["authors"] = ev["authors"][:_MAX_EVIDENCE_AUTHORS]

    # Step 5+6: Normalize to a bounded share, filter, and build results
    results: list[CrossRepoCoChange] = []
    for (src_repo, src_file, tgt_repo, tgt_file), score in pair_scores.items():
        freq = pair_freq[(src_repo, src_file, tgt_repo, tgt_file)]
        if freq < min_sessions:
            continue
        denom = (
            min(
                file_activity[(src_repo, src_file)],
                file_activity[(tgt_repo, tgt_file)],
            )
            + _STRENGTH_SMOOTHING
        )
        strength = score / denom
        if strength < min_score:
            continue
        last_ts = pair_last_ts.get((src_repo, src_file, tgt_repo, tgt_file), 0)
        last_date = (
            datetime.fromtimestamp(last_ts, tz=UTC).strftime("%Y-%m-%d") if last_ts > 0 else ""
        )
        ev = pair_evidence.get((src_repo, src_file, tgt_repo, tgt_file))
        evidence = None
        if ev and (ev["commit_pairs"] or ev["authors"]):
            evidence = CoChangeEvidence(
                authors=ev["authors"][:_MAX_EVIDENCE_AUTHORS],
                commit_pairs=ev["commit_pairs"][:_MAX_EVIDENCE_COMMIT_PAIRS],
                max_gap_hours=round(ev["max_gap_hours"], 1),
            )
        results.append(
            CrossRepoCoChange(
                source_repo=src_repo,
                source_file=src_file,
                target_repo=tgt_repo,
                target_file=tgt_file,
                strength=round(strength, 3),
                frequency=freq,
                last_date=last_date,
                evidence=evidence,
            )
        )

    # Sort by strength (frequency breaking ties), cap per repo pair so one
    # hyperactive pair cannot starve the rest, then cap globally.
    results.sort(key=lambda x: (-x.strength, -x.frequency))
    per_pair: dict[tuple[str, str], int] = defaultdict(int)
    capped: list[CrossRepoCoChange] = []
    for r in results:
        pair_key = (r.source_repo, r.target_repo)
        if per_pair[pair_key] >= MAX_EDGES_PER_REPO_PAIR:
            continue
        per_pair[pair_key] += 1
        capped.append(r)
        if len(capped) >= MAX_EDGES:
            break
    if len(capped) < len(results):
        _log.info(
            "Co-change mining kept %d of %d qualifying pairs (caps: %d total, %d per repo pair)",
            len(capped),
            len(results),
            MAX_EDGES,
            MAX_EDGES_PER_REPO_PAIR,
        )
    return capped, len(results)


# ---------------------------------------------------------------------------
# Repo summaries
# ---------------------------------------------------------------------------


def _build_repo_summaries(
    repo_paths: dict[str, Path],
    co_changes: list[CrossRepoCoChange],
    package_deps: list[CrossRepoPackageDep],
    package_diagnostics: list[CrossRepoPackageDiagnostic] | None = None,
) -> dict[str, dict]:
    """Build per-repo summary stats."""
    summaries: dict[str, dict] = {}

    # Count cross-repo edges per repo
    edge_counts: dict[str, int] = defaultdict(int)
    for cc in co_changes:
        edge_counts[cc.source_repo] += 1
        edge_counts[cc.target_repo] += 1
    for pd in package_deps:
        edge_counts[pd.source_repo] += 1
        edge_counts[pd.target_repo] += 1

    diagnostic_counts: dict[str, int] = defaultdict(int)
    diagnostic_codes: dict[str, set[str]] = defaultdict(set)
    for diagnostic in package_diagnostics or []:
        diagnostic_counts[diagnostic.repo] += 1
        diagnostic_codes[diagnostic.repo].add(diagnostic.code)

    for alias in repo_paths:
        summaries[alias] = {
            "cross_repo_edge_count": edge_counts.get(alias, 0),
            "package_diagnostics_emitted": diagnostic_counts.get(alias, 0),
            "package_diagnostic_codes": sorted(diagnostic_codes.get(alias, set())),
        }

    return summaries


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_overlay(overlay: CrossRepoOverlay, workspace_root: Path) -> Path:
    """Save overlay to ``.repowise-workspace/cross_repo_edges.json``."""
    data_dir = ensure_workspace_data_dir(workspace_root)
    out_path = data_dir / CROSS_REPO_EDGES_FILENAME
    # Atomic: the MCP enricher reads these artifacts from a separate
    # process and must never observe a half-written file.
    atomic_write_text(out_path, json.dumps(overlay.to_dict(), indent=2, ensure_ascii=False))
    return out_path


def load_overlay(workspace_root: Path) -> CrossRepoOverlay | None:
    """Load overlay from ``.repowise-workspace/cross_repo_edges.json``.

    Returns ``None`` if the file doesn't exist or is corrupt.
    """
    from .config import WORKSPACE_DATA_DIR

    path = workspace_root / WORKSPACE_DATA_DIR / CROSS_REPO_EDGES_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version", 1) < _OVERLAY_VERSION:
            # Older overlays used unbounded strength values; consumers now
            # assume the bounded [0, 1) share, so treat them as absent until
            # the next workspace update regenerates the file.
            _log.info(
                "Ignoring cross-repo overlay at %s (version %s < %s)",
                path,
                data.get("version", 1),
                _OVERLAY_VERSION,
            )
            return None
        return CrossRepoOverlay.from_dict(data)
    except Exception:
        _log.warning("Failed to load cross-repo overlay from %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_cross_repo_analysis(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_repos: list[str],
) -> CrossRepoOverlay:
    """Full cross-repo analysis pipeline.

    Called from :func:`run_cross_repo_hooks` after workspace update.
    """
    # Build repo_paths dict — only include repos that have been indexed
    # (have a .repowise/ directory). Non-indexed repos must not leak into
    # cross-repo signals.
    repo_paths: dict[str, Path] = {}
    for entry in ws_config.repos:
        abs_path = (workspace_root / entry.path).resolve()
        if abs_path.is_dir() and (abs_path / ".repowise").is_dir():
            repo_paths[entry.alias] = abs_path
        elif abs_path.is_dir():
            _log.debug(
                "Skipping non-indexed repo %r in cross-repo analysis",
                entry.alias,
            )

    if len(repo_paths) < 2:
        _log.debug("Skipping cross-repo analysis — fewer than 2 indexed repos")
        return CrossRepoOverlay()

    _log.info(
        "Running cross-repo analysis across %d repos (changed: %s)",
        len(repo_paths),
        ", ".join(changed_repos),
    )

    # Co-change detection (CPU-bound git subprocess calls)
    import asyncio

    co_changes, total_co_changes = await asyncio.to_thread(detect_cross_repo_co_changes, repo_paths)

    # Package dependency detection (file I/O)
    package_deps, package_diagnostics, total_package_diagnostics = await asyncio.to_thread(
        detect_package_dependencies_with_diagnostics,
        repo_paths,
    )

    # Build summaries
    repo_summaries = _build_repo_summaries(
        repo_paths,
        co_changes,
        package_deps,
        package_diagnostics,
    )

    overlay = CrossRepoOverlay(
        version=_OVERLAY_VERSION,
        generated_at=datetime.now(UTC).isoformat(),
        co_changes=co_changes,
        package_deps=package_deps,
        package_diagnostics=package_diagnostics,
        repo_summaries=repo_summaries,
        total_co_changes=total_co_changes,
        total_package_diagnostics=total_package_diagnostics,
    )

    # Persist
    out_path = save_overlay(overlay, workspace_root)
    _log.info(
        "Cross-repo analysis complete: %d co-change edges, %d package deps → %s",
        len(co_changes),
        len(package_deps),
        out_path,
    )

    return overlay

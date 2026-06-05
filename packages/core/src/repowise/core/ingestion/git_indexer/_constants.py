"""Module constants, regexes, and the GitPython noise patch for git indexing.

Kept in one place so the per-tier modules share a single source of truth for
the commit-depth defaults, decay half-lives, and skip heuristics.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

from ..languages.registry import REGISTRY as _LANG_REGISTRY

# Silence GitPython's _CatFileContentStream.__del__ ValueError spam.
# When git cat-file streams are GC'd after the subprocess pipe is closed,
# their __del__ tries to drain remaining bytes and hits a closed file.
# This is harmless but floods stderr with tracebacks.
try:
    from git.cmd import _CatFileContentStream

    _orig_del = _CatFileContentStream.__del__

    def _quiet_del(self: Any) -> None:
        with contextlib.suppress(ValueError, OSError):
            _orig_del(self)

    _CatFileContentStream.__del__ = _quiet_del  # type: ignore[assignment]
except Exception:
    pass  # git not installed — nothing to patch

# Commit message prefixes that are ALWAYS skipped (no signal value).
_HARD_SKIP_PREFIXES = ("Merge ",)

# Conventional-commit prefixes normally skipped — but kept if the message
# contains a decision-signal keyword (e.g. "build: migrate from webpack to vite").
_SOFT_SKIP_PREFIXES = ("Bump ", "chore:", "ci:", "style:", "build:", "release:")

# Lightweight subset of decision-signal keywords (mirrors decision_extractor.py).
# Used to rescue soft-skipped commits that carry architectural intent.
_DECISION_SIGNAL_WORDS: frozenset[str] = frozenset(
    {
        "migrate",
        "migration",
        "switch to",
        "replace",
        "refactor",
        "adopt",
        "introduce",
        "deprecate",
        "remove",
        "upgrade",
        "rewrite",
        "extract",
        "convert",
        "transition",
    }
)

_SKIP_AUTHORS = ("dependabot", "renovate", "github-actions")
_MIN_MESSAGE_LEN = 12

# Default per-file commit history depth.
_DEFAULT_COMMIT_LIMIT: int = 500

# Per-file persisted contributor / commit fan-out. Previously hard-coded
# to 5 / 10 inline, which silently hid co-owners and meaningful history on
# multi-team modules. 50 is generous enough that any realistic UI surface
# can render the full list while keeping the JSON blob bounded.
_MAX_TOP_AUTHORS: int = 50
_MAX_SIGNIFICANT_COMMITS: int = 50

# Byte ceiling for the commit body (``%b``) retained on a significant-commit
# entry. Squash-merge repos put the whole "why" in the body, which unlocks
# PR/squash decision mining. But the body is stored once *per file the commit
# touched*, so a wide squash commit duplicates it many times — measuring a real
# repo showed a 2 KB cap inflating ``significant_commits_json`` ~8x. 1 KB holds
# the decision-bearing lead of a PR description (## Why / ## Motivation /
# before-after) while halving that overhead, and bodies are further gated to
# commits that actually look like decisions (see ``_body_carries_decision``).
# Truncation is byte-accurate (UTF-8) so the cap is a real storage ceiling.
_MAX_COMMIT_BODY_BYTES: int = 1024

# PR/squash-description markers — a body containing one of these reads like a
# real PR write-up worth retaining for decision mining (mirrors the extractor's
# ``_PR_BODY_MARKERS``; kept here to avoid a cross-package import at index time).
_PR_BODY_MARKERS: tuple[str, ...] = (
    "## why",
    "## motivation",
    "## what",
    "## changes",
    "## context",
    "## summary",
    "closes #",
    "fixes #",
    "resolves #",
    "before:",
    "after:",
)

# Co-change pair extraction widens the window because individual files
# may only co-change a handful of times in 500 commits — well below the
# ``min_count`` threshold. On low-churn repos the 500-commit window
# produced 0 co-change pairs every run; 2000 commits captures enough
# history for the decay-weighted score to clear the bar without
# meaningfully blowing up wall-clock time (single `git log` call).
_DEFAULT_CO_CHANGE_COMMIT_LIMIT: int = 2000

# Minimum decay-weighted co-occurrence weight for a pair to be recorded.
# Was 3 historically; on repos with sparse change history (libraries,
# stable services) that produced empty co-change tables. Two recent
# co-changes is enough signal to surface in the UI, and the dashboard
# already sorts partners by weight so the ranking is unaffected.
_DEFAULT_CO_CHANGE_MIN_COUNT: int = 2

# Commits that touch a very large number of files (mass renames,
# copyright header sweeps, code-mod runs) produce O(N^2) pairs and
# contribute no useful co-change signal. Skip pair generation for any
# commit above this threshold. The decay-weighted score already
# de-prioritises mass-edit commits, but the pairs are materialised
# first — for a worst-case 500 files/commit x 2000 commits run that's
# 250M pairs ~= 16 GB RAM. The cap is a memory safeguard, not a
# correctness change: typical commits sit well under 20 files.
_MAX_FILES_PER_COMMIT_FOR_COCHANGE: int = 200

# Change-entropy uses a tighter file-set cap than co-change. Hassan (2009)
# excludes very wide commits from the History Complexity Metric because a
# sweeping edit spreads its "change probability" so thinly that it adds noise
# rather than signal. 30 follows the commonly cited Hassan filter; commits
# above it are dropped from the entropy accumulation entirely.
_MAX_FILES_PER_COMMIT_FOR_ENTROPY: int = 30

# Commit message classification regexes (Phase 2.2).
_COMMIT_CATEGORIES: dict[str, re.Pattern[str]] = {
    "feature": re.compile(
        r"\b(add|implement|introduce|create|new|feat)\b",
        re.IGNORECASE,
    ),
    "refactor": re.compile(
        r"\b(refactor|restructure|cleanup|clean.up|rename|reorganize|extract|simplify|move)\b",
        re.IGNORECASE,
    ),
    "fix": re.compile(
        r"\b(fix|bug|patch|hotfix|revert|regression|broken|crash|error)\b",
        re.IGNORECASE,
    ),
    "dependency": re.compile(
        r"\b(upgrade|bump|update.dep|migrate.to|switch.to|dependency|dependencies)\b",
        re.IGNORECASE,
    ),
}

# Bug-fix commit classifier — mirrors the defect benchmark's
# ``lib/defect_counter.find_fix_commits`` (keyword strategy) so the product's
# ``prior_defect`` signal counts exactly the commits the benchmark labels as
# fixes (product == benchmark). A commit subject is a fix iff it matches an
# INCLUDE pattern and NO EXCLUDE pattern; merge commits are excluded upstream
# (the per-file walk skips ``is_merge``), mirroring the bench's ``--no-merges``.
#
# Deliberately NOT reusing ``_COMMIT_CATEGORIES["fix"]`` — that is a broader
# classifier tuned for commit-category *ratios* (it catches "refactor to fix
# crash", "error handling"), whereas the defect label wants high-precision
# fix-only matches and must stay byte-identical to the benchmark's regex set.
_FIX_COMMIT_INCLUDE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfix\b", re.IGNORECASE),
    re.compile(r"\bbug\b", re.IGNORECASE),
    re.compile(r"\bpatch\b", re.IGNORECASE),
    re.compile(r"\bresolves?\b", re.IGNORECASE),
    re.compile(r"closes?\s+#\d+", re.IGNORECASE),
    re.compile(r"fixes?\s+#\d+", re.IGNORECASE),
)
_FIX_COMMIT_EXCLUDE: tuple[re.Pattern[str], ...] = (
    re.compile(r"^Merge ", re.IGNORECASE),
    re.compile(r"\btypo\b", re.IGNORECASE),
    re.compile(r"\bbump\b", re.IGNORECASE),
    re.compile(r"\bdeps?\b", re.IGNORECASE),
    re.compile(r"\bchore\b", re.IGNORECASE),
    re.compile(r"\blint\b", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bstyle\b", re.IGNORECASE),
    re.compile(r"\bdocs?\b", re.IGNORECASE),
)

# Trailing window over which ``prior_defect`` counts bug-fix commits. 180 days
# (≈6 months) intentionally matches the benchmark's ``defect_window_months: 6``
# prior-defects baseline — a wider window than the 90d activity signals because
# defect history is a slower-moving cluster than recent churn.
PRIOR_DEFECT_WINDOW_DAYS: int = 180


def is_fix_commit(subject: str) -> bool:
    """Whether a commit *subject* is a bug-fix, per the benchmark's keyword rule."""
    if not subject:
        return False
    if any(p.search(subject) for p in _FIX_COMMIT_EXCLUDE):
        return False
    return any(p.search(subject) for p in _FIX_COMMIT_INCLUDE)


# Co-change temporal decay: half-life ~125 days (lambda for exp(-t/tau)).
_CO_CHANGE_DECAY_TAU: float = 180.0

# Hotspot temporal decay: half-life for exponentially weighted churn score.
HOTSPOT_HALFLIFE_DAYS: float = 180.0

# Absolute activity floors for hotspot classification (issue #361). The
# churn percentile is repo-relative, so on a quiet repo "top quartile"
# degenerates to "any file touched in the last 90 days" — a single drive-by
# maintenance commit was enough to flag a hotspot. A file must clear BOTH
# the relative gate (top-quartile decayed churn) and these absolute floors:
#
# - at least HOTSPOT_MIN_COMMITS_90D commits in the window (repeated
#   recent activity, not one drive-by), AND
# - a decayed-churn score of at least HOTSPOT_MIN_TEMPORAL_SCORE (the
#   commits moved real lines — e.g. one ~50-line change today, or ~3
#   focused 20-line changes this month — not a string of one-liners),
#   OR HOTSPOT_HIGH_COMMITS_90D+ commits in the window (sustained high
#   commit volume is hotspot-grade activity even when numstat line counts
#   are unavailable, e.g. binary files). The 8-commit escape matches the
#   threshold the health biomarkers already treat as hotspot-equivalent.
#
# Mirrored in the SQL PERCENT_RANK path (crud/git.py::recompute_git_percentiles)
# — keep the two in sync.
HOTSPOT_MIN_COMMITS_90D: int = 3
HOTSPOT_MIN_TEMPORAL_SCORE: float = 0.5
HOTSPOT_HIGH_COMMITS_90D: int = 8

# Regex to extract PR/MR numbers from commit messages.
# Matches: "#123", "Merge pull request #456", "(#789)", "!42" (GitLab MR)
_PR_NUMBER_RE = re.compile(r"(?:pull request |)\#(\d+)|\(#(\d+)\)|!(\d+)")

# Allowlist of extensions for which per-file git indexing (blame, commit
# history, hotspot/stable classification) is worth running.  Anything NOT in
# this set is skipped — data, config, markup, dotfiles, and binaries add no
# documentation value, and git blame on large JSON/YAML files is very slow.
# Co-change detection still runs across ALL tracked files regardless.
# Derived from the centralised LanguageRegistry — all code language extensions.
_CODE_EXTENSIONS: frozenset[str] = _LANG_REGISTRY.all_code_extensions()

# Files larger than this skip git blame.  blame is O(lines) and blocks the
# executor thread — for large files the commit-based ownership estimate is
# used as a fallback instead.
_MAX_BLAME_SIZE_BYTES: int = 100 * 1024  # 100 KB

# Maximum seconds to wait for a single file's git indexing.  If exceeded the
# file is recorded with whatever data was collected before the timeout and the
# semaphore slot is released so other files can proceed.
_FILE_INDEX_TIMEOUT_SECS: float = 45.0

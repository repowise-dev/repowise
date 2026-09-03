"""Repo-relative baseline sampling for change-risk percentiles.

Scores a repo's recent commits so a single change's raw risk score can be
ranked against them (see :mod:`.normalize`). Lives in core (not the CLI) so
both the CLI and the server can build a percentile off the same live-git
sample without duplicating the walk.
"""

from __future__ import annotations

import subprocess
from typing import NamedTuple

import pathspec

from .features import GIT_TIMEOUT_SECONDS, _git, features_from_file_changes
from .fix_history import change_fix_density
from .model import score_change


class BaselineSample(NamedTuple):
    """One sampled commit: its sha, its diff-shape score, and its file churn.

    ``file_churn`` rides along so a caller can compute the commit's fix density
    against a pressure map without a second walk — the sample is the only place
    the sampled commits' paths exist.
    """

    sha: str
    score: float
    file_churn: tuple[tuple[str, int], ...]

# Process-wide memo for the 200-commit baseline walk, which is the dominant cost
# of a default get_change_risk call and is identical for every change scored
# against the same repo state. Keyed on the *resolved anchor sha* (not the ref
# name) so a new commit on HEAD busts the entry, plus every other input that
# changes the sample (sample size, filters). Deliberately NOT keyed on the
# target being scored: the sample is stored whole and the target's own score is
# dropped when ranking, so two changes against the same history share one walk.
_BASELINE_CACHE: dict[tuple, list[BaselineSample]] = {}
# Crude bound so a long-lived MCP server that scores many distinct changes does
# not grow the memo without limit. On overflow the whole cache is dropped
# (correctness is unaffected; the next call just recomputes). Upgrade to an LRU
# only if profiling shows the drop-all churn matters.
_BASELINE_CACHE_MAX = 256


def clear_baseline_cache() -> None:
    """Drop all memoized baseline samples (test isolation / manual reset)."""
    _BASELINE_CACHE.clear()


def _resolve_anchor_sha(repo_path: str, anchor: str) -> str | None:
    """Resolve *anchor* to a full sha for cache keying, or None if it cannot be.

    check=False: a bad anchor is not fatal here - it just means we skip caching
    and let :func:`baseline_samples` compute (and degrade) as it normally would.
    """
    sha = _git(["rev-parse", "--verify", "--quiet", anchor], repo_path, check=False).strip()
    return sha or None


def _retained(samples: list[BaselineSample], excluded_ref: str) -> list[BaselineSample]:
    """The sample with the target commit's own entry removed.

    Self-exclusion is a rank-time filter, not a property of the sample, so the
    walk is cached whole and each target removes only itself. *excluded_ref* is
    a commit sha, full or abbreviated - a ref *name* matches nothing, since the
    sample is keyed by sha. Empty means nothing to exclude (a range or an
    uncommitted change is not in the sample to begin with). An abbreviation
    short enough to prefix-match several shas will drop all of them.
    """
    if not excluded_ref:
        return list(samples)
    return [
        sample
        for sample in samples
        if not (sample.sha.startswith(excluded_ref) or excluded_ref.startswith(sample.sha))
    ]


def scores_excluding(samples: list[BaselineSample], excluded_ref: str) -> list[float]:
    """Diff-shape scores of the sample, minus the target commit's own."""
    return [sample.score for sample in _retained(samples, excluded_ref)]


def densities_excluding(
    samples: list[BaselineSample], excluded_ref: str, pressure: dict[str, float]
) -> list[float]:
    """Fix densities of the sample, minus the target commit's own.

    Every sampled commit is measured against the *same* pressure map the target
    is, so the population and the target share a unit. That map is read once at
    the target's history ref rather than rebuilt per sampled commit: a per-commit
    walk would cost one deep git log each, and the question being asked is where
    this change's ground sits on today's map, not on each commit's own.

    That does leave one asymmetry. The map is cut strictly before the target, so
    the target is never credited with its own fixes, while a sampled commit is
    measured on a map that already holds the fixes landing after it. Population
    densities therefore read slightly high and the target's rank slightly low,
    growing with how far the sample reaches past the half-life. Correcting it
    means one deep walk per sampled commit, which is the cost this whole design
    exists to avoid.
    """
    return [
        change_fix_density(pressure, sample.file_churn)
        for sample in _retained(samples, excluded_ref)
    ]


def baseline_samples(
    repo_path: str,
    anchor: str,
    limit: int,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...] = (),
) -> list[BaselineSample]:
    """Score the repo's recent commits to build a local risk distribution.

    Returns :class:`BaselineSample` rows so a caller can exclude the change it
    is ranking (see :func:`scores_excluding`) without needing a sample of its
    own, and can rank a fix density against the same commits
    (see :func:`densities_excluding`).

    One ``git log --numstat`` call (no per-commit author lookup), so it stays
    cheap enough for a pre-merge gate. Experience is left unknown for the
    baseline; the target is ranked with experience likewise unknown, so the
    comparison is like-with-like: a diff-shape percentile within this repo.
    *exclude_patterns* use gitignore syntax and are applied to every sampled
    commit, matching the target change's filtering.
    """
    # stdin=DEVNULL + timeout: a stuck git must not hang the caller (on MCP
    # stdio transport an inherited pipe handle can wedge the whole session).
    # No returncode check: the anchor was already validated by the feature
    # extraction, and a failed sample degrades honestly to "no percentile".
    out = subprocess.run(
        ["git", "log", f"-n{limit}", "--no-merges", "--format=%x1e%H", "--numstat", anchor],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
    ).stdout

    samples: list[BaselineSample] = []
    exclude_spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns)
    for block in out.split("\x1e"):
        lines = block.strip().split("\n")
        if not lines or not lines[0]:
            continue
        sha, rows = lines[0].strip(), lines[1:]
        changes: list[tuple[str, int, int]] = []
        for row in rows:
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            a_raw, d_raw, path = parts
            if extensions and not path.endswith(extensions):
                continue
            if exclude_spec.match_file(path):
                continue
            a = int(a_raw) if a_raw.isdigit() else 0
            d = int(d_raw) if d_raw.isdigit() else 0
            changes.append((path, a, d))
        if not changes:
            continue
        feats = features_from_file_changes(changes, exp=None)
        samples.append(BaselineSample(sha, score_change(feats).score, feats.file_churn))
    return samples


def baseline_samples_cached(
    repo_path: str,
    anchor: str,
    limit: int,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...] = (),
) -> list[BaselineSample]:
    """Memoized :func:`baseline_samples`, keyed on the resolved anchor sha.

    Same result as :func:`baseline_samples` for the same inputs; it just skips
    the 200-commit git walk when an identical sample was already computed this
    process. The anchor is resolved to a sha so ``HEAD`` (or a branch ref) busts
    the entry as soon as a new commit lands. When the anchor cannot be resolved
    the call falls through to an uncached computation.
    """
    sha = _resolve_anchor_sha(repo_path, anchor)
    if sha is None:
        return baseline_samples(repo_path, anchor, limit, extensions, exclude_patterns)
    key = (repo_path, sha, limit, extensions, exclude_patterns)
    if key in _BASELINE_CACHE:
        return _BASELINE_CACHE[key]
    samples = baseline_samples(repo_path, anchor, limit, extensions, exclude_patterns)
    if len(_BASELINE_CACHE) >= _BASELINE_CACHE_MAX:
        _BASELINE_CACHE.clear()
    _BASELINE_CACHE[key] = samples
    return samples

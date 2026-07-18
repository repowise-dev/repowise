"""Repo-relative baseline sampling for change-risk percentiles.

Scores a repo's recent commits so a single change's raw risk score can be
ranked against them (see :mod:`.normalize`). Lives in core (not the CLI) so
both the CLI and the server can build a percentile off the same live-git
sample without duplicating the walk.
"""

from __future__ import annotations

import subprocess

import pathspec

from .features import GIT_TIMEOUT_SECONDS, _git, features_from_file_changes
from .model import score_change

# Process-wide memo for the 200-commit baseline walk, which is the dominant cost
# of a default get_change_risk call and is identical for every change scored
# against the same repo state. Keyed on the *resolved anchor sha* (not the ref
# name) so a new commit on HEAD busts the entry, plus every other input that
# changes the sample (sample size, filters, the self-excluded ref).
_BASELINE_CACHE: dict[tuple, list[float]] = {}
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
    and let :func:`baseline_scores` compute (and degrade) as it normally would.
    """
    sha = _git(["rev-parse", "--verify", "--quiet", anchor], repo_path, check=False).strip()
    return sha or None


def baseline_scores(
    repo_path: str,
    anchor: str,
    limit: int,
    extensions: tuple[str, ...],
    excluded_ref: str,
    exclude_patterns: tuple[str, ...] = (),
) -> list[float]:
    """Score the repo's recent commits to build a local risk distribution.

    One ``git log --numstat`` call (no per-commit author lookup), so it stays
    cheap enough for a pre-merge gate. Experience is left unknown for the
    baseline; the target is ranked with experience likewise unknown, so the
    comparison is like-with-like: a diff-shape percentile within this repo.
    *excluded_ref* is a full or abbreviated Git ref for the target commit to
    omit from its own sample. It is unrelated to path exclusions.
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

    scores: list[float] = []
    exclude_spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns)
    for block in out.split("\x1e"):
        lines = block.strip().split("\n")
        if not lines or not lines[0]:
            continue
        sha, rows = lines[0].strip(), lines[1:]
        # Do not let the target commit rank against itself (short or full ref).
        if excluded_ref and (sha.startswith(excluded_ref) or excluded_ref.startswith(sha)):
            continue
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
        scores.append(score_change(feats).score)
    return scores


def baseline_scores_cached(
    repo_path: str,
    anchor: str,
    limit: int,
    extensions: tuple[str, ...],
    excluded_ref: str,
    exclude_patterns: tuple[str, ...] = (),
) -> list[float]:
    """Memoized :func:`baseline_scores`, keyed on the resolved anchor sha.

    Same result as :func:`baseline_scores` for the same inputs; it just skips the
    200-commit git walk when an identical sample was already computed this
    process. The anchor is resolved to a sha so ``HEAD`` (or a branch ref) busts
    the entry as soon as a new commit lands. When the anchor cannot be resolved
    the call falls through to an uncached computation.
    """
    sha = _resolve_anchor_sha(repo_path, anchor)
    if sha is None:
        return baseline_scores(
            repo_path, anchor, limit, extensions, excluded_ref, exclude_patterns
        )
    key = (repo_path, sha, limit, extensions, excluded_ref, exclude_patterns)
    if key in _BASELINE_CACHE:
        return _BASELINE_CACHE[key]
    scores = baseline_scores(repo_path, anchor, limit, extensions, excluded_ref, exclude_patterns)
    if len(_BASELINE_CACHE) >= _BASELINE_CACHE_MAX:
        _BASELINE_CACHE.clear()
    _BASELINE_CACHE[key] = scores
    return scores

"""Shared live change-risk orchestration for CLI and MCP surfaces."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from .baseline import baseline_samples_cached, scores_excluding
from .features import (
    GIT_TIMEOUT_SECONDS,
    ChangeFeatures,
    _git,
    extract_commit_features,
    extract_range_features,
    extract_worktree_features,
    working_tree_is_dirty,
)
from .model import SCORE_UNIT, ChangeRisk, score_change
from .normalize import RiskNormalizer, review_priority_classification

_MIN_BASELINE = 8


@dataclass(frozen=True)
class ChangeRiskResult:
    """A live change score and its optional repository-relative ranking."""

    features: ChangeFeatures
    risk: ChangeRisk
    percentile: float | None
    priority: str | None
    baseline_sample_size: int
    riskignore_excludes: tuple[str, ...]
    request_excludes: tuple[str, ...]
    working_tree: bool = False  # scored the uncommitted change, not a commit


def riskignore_patterns(repo_path: str) -> tuple[str, ...]:
    """Load non-comment patterns from the repository-root ``.riskignore``."""
    proc = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        return ()
    ignore_file = Path(proc.stdout.strip()) / ".riskignore"
    if not ignore_file.is_file():
        return ()
    return tuple(
        line
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def _commit_anchor(repo_path: str, target: str) -> tuple[str, str]:
    """Return ``(anchor, resolved sha)`` for a commit target.

    A commit in ``HEAD``'s history is ranked against the repo's current sample
    rather than building a private one, so every such target in a process shares
    a single walk. A commit that is not (another branch, an unrelated ref) keeps
    its own anchor, since HEAD's history is not its cohort.

    The sha comes back because self-exclusion needs it: the sample holds commit
    shas, while the target is whatever the caller spelled — ``"HEAD"`` most of
    the time, which matches no sha and so excluded nothing.
    """
    # ^{commit} peels an annotated tag to the commit it points at. Without it the
    # tag object's own sha comes back, which is in no sample and is never equal
    # to a merge-base, so both the anchor and the self-exclusion below miss.
    target_sha = _git(
        ["rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"], repo_path, check=False
    ).strip()
    if target == "HEAD" or not target_sha:
        return "HEAD" if target == "HEAD" else target, target_sha
    # merge-base == the target itself is exactly "target is an ancestor of HEAD",
    # read off stdout because _git does not surface a return code.
    merge_base = _git(["merge-base", target, "HEAD"], repo_path, check=False).strip()
    return ("HEAD" if merge_base == target_sha else target), target_sha


def range_anchor(repo_path: str, base: str, head: str) -> str:
    """Anchor a range's baseline to where its two sides diverged.

    Keeps the range's own commits out of the distribution it is measured
    against, and lets ranges off the same fork point share one memoized walk.
    Note this also drops commits that landed on *base* after the fork: they are
    cohort members, but including them would mean re-walking per head.
    """
    merge_base = _git(["merge-base", base, head], repo_path, check=False).strip()
    return merge_base or base


def normalize_extensions(extensions: tuple[str, ...]) -> tuple[str, ...]:
    """Add a leading dot to requested suffixes, matching the CLI contract."""
    return tuple(ext if ext.startswith(".") else f".{ext}" for ext in extensions)


def score_live_change(
    repo_path: str,
    revspec: str | None = None,
    *,
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
    baseline: int = 200,
) -> ChangeRiskResult:
    """Score a commit or ``base..head`` range with optional live filters.

    With no *revspec* the subject is "the change in front of me": the
    uncommitted work if the tree is dirty, otherwise ``HEAD``. A caller that
    just wrote code and asked for a score means the code it wrote, and the
    previous commit is the one answer that is certainly wrong. An explicit
    *revspec* — ``"HEAD"`` included — always means committed refs.
    """
    if baseline < 0:
        raise ValueError("baseline must be non-negative")

    extensions = normalize_extensions(extensions)
    from_riskignore = riskignore_patterns(repo_path)
    effective_excludes = from_riskignore + exclude_patterns
    target = revspec or "HEAD"
    uncommitted = (
        extract_worktree_features(
            repo_path, extensions=extensions, exclude_patterns=effective_excludes
        )
        if revspec is None and working_tree_is_dirty(repo_path)
        else None
    )
    # A tree dirty only in paths the filters drop is not a change this command
    # can score, so fall through to HEAD rather than answer "empty change".
    working_tree = uncommitted is not None and uncommitted.nf > 0
    if working_tree:
        features = uncommitted
        anchor, excluded_ref = "HEAD", ""
    elif ".." in target:
        base, _, head = target.partition("..")
        # Strip leading dot(s) so three-dot syntax (main...HEAD) gives a valid anchor ref.
        head = head.lstrip(".") or "HEAD"
        features = extract_range_features(
            repo_path, base, head, extensions=extensions, exclude_patterns=effective_excludes
        )
        anchor, excluded_ref = range_anchor(repo_path, base, head), ""
    else:
        features = extract_commit_features(
            repo_path, target, extensions=extensions, exclude_patterns=effective_excludes
        )
        anchor, excluded_ref = _commit_anchor(repo_path, target)

    risk = score_change(features)
    percentile: float | None = None
    priority: str | None = None
    baseline_sample_size = 0
    if baseline:
        samples = baseline_samples_cached(
            repo_path,
            anchor,
            baseline,
            extensions,
            exclude_patterns=effective_excludes,
        )
        scores = scores_excluding(samples, excluded_ref)
        baseline_sample_size = len(scores)
        if len(scores) >= _MIN_BASELINE:
            normalizer = RiskNormalizer.from_scores(scores)
            rank_score = score_change(replace(features, exp=None)).score
            percentile = normalizer.percentile(rank_score)
            priority = normalizer.priority(rank_score)

    return ChangeRiskResult(
        features=features,
        risk=risk,
        percentile=percentile,
        priority=priority,
        baseline_sample_size=baseline_sample_size,
        riskignore_excludes=from_riskignore,
        request_excludes=exclude_patterns,
        working_tree=working_tree,
    )


def change_risk_payload(result: ChangeRiskResult) -> dict:
    """Render the machine-readable response shared by the CLI and MCP tool.

    ``fallback_band`` is the absolute calibrated band, non-null only when there
    was no baseline to rank against — which is why it is not a peer of
    ``review_priority``. ``score_unit`` names the unit that band assumes.
    """
    features, risk = result.features, result.risk
    return {
        "ref": features.ref,
        "working_tree": result.working_tree,
        "score": risk.score,
        "score_unit": SCORE_UNIT,
        "risk_percentile": round(result.percentile, 1) if result.percentile is not None else None,
        "review_priority": result.priority,
        "classification": review_priority_classification(result.priority),
        "fallback_band": risk.level if result.priority is None else None,
        "baseline_sample_size": result.baseline_sample_size,
        "exclude_patterns": list(result.riskignore_excludes + result.request_excludes),
        "is_fix": features.is_fix,
        "features": {
            "la": features.la,
            "ld": features.ld,
            "nf": features.nf,
            "nd": features.nd,
            "ns": features.ns,
            "entropy": round(features.entropy, 4),
            "exp": features.exp,
        },
        "drivers": [
            {
                "feature": driver.feature,
                "value": driver.value,
                "contribution": round(driver.contribution, 4),
                "label": driver.label,
            }
            for driver in risk.top_drivers
        ],
    }

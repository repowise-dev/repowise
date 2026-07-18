"""Shared live change-risk orchestration for CLI and MCP surfaces."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from .baseline import baseline_scores_cached
from .features import (
    GIT_TIMEOUT_SECONDS,
    ChangeFeatures,
    extract_commit_features,
    extract_range_features,
)
from .model import ChangeRisk, score_change
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


def normalize_extensions(extensions: tuple[str, ...]) -> tuple[str, ...]:
    """Add a leading dot to requested suffixes, matching the CLI contract."""
    return tuple(ext if ext.startswith(".") else f".{ext}" for ext in extensions)


def score_live_change(
    repo_path: str,
    revspec: str = "HEAD",
    *,
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
    baseline: int = 200,
) -> ChangeRiskResult:
    """Score a commit or ``base..head`` range with optional live filters."""
    if baseline < 0:
        raise ValueError("baseline must be non-negative")

    extensions = normalize_extensions(extensions)
    from_riskignore = riskignore_patterns(repo_path)
    effective_excludes = from_riskignore + exclude_patterns
    if ".." in revspec:
        base, _, head = revspec.partition("..")
        # Strip leading dot(s) so three-dot syntax (main...HEAD) gives a valid anchor ref.
        head = head.lstrip(".") or "HEAD"
        features = extract_range_features(
            repo_path, base, head, extensions=extensions, exclude_patterns=effective_excludes
        )
        anchor, excluded_ref = head, ""
    else:
        features = extract_commit_features(
            repo_path, revspec, extensions=extensions, exclude_patterns=effective_excludes
        )
        anchor, excluded_ref = revspec, features.ref

    risk = score_change(features)
    percentile: float | None = None
    priority: str | None = None
    baseline_sample_size = 0
    if baseline:
        scores = baseline_scores_cached(
            repo_path,
            anchor,
            baseline,
            extensions,
            excluded_ref=excluded_ref,
            exclude_patterns=effective_excludes,
        )
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
    )


def change_risk_payload(result: ChangeRiskResult) -> dict:
    """Render the machine-readable response shared by the CLI and MCP tool."""
    features, risk = result.features, result.risk
    return {
        "ref": features.ref,
        "score": risk.score,
        "probability": round(risk.probability, 4),
        "level": risk.level,
        "risk_percentile": round(result.percentile, 1) if result.percentile is not None else None,
        "review_priority": result.priority,
        "classification": review_priority_classification(result.priority),
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

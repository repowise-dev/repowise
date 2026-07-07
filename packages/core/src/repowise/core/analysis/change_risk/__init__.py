"""Just-in-time change-risk scoring.

Scores a *change* (a commit or a ``base..head`` range) for defect risk from its
diff shape — size, diffusion, authorship — using a linear, interpretable model
with offline-calibrated constants. Complements the file-level health score: it
is not size-dominated, so it flags risky *small* changes the file delta misses,
and is a natural pre-merge / PR gate.
"""

from __future__ import annotations

from .baseline import baseline_scores
from .features import (
    ChangeFeatures,
    extract_commit_features,
    extract_range_features,
    features_from_file_changes,
)
from .model import ChangeRisk, RiskDriver, score_change
from .normalize import RiskNormalizer

__all__ = [
    "ChangeFeatures",
    "ChangeRisk",
    "RiskDriver",
    "RiskNormalizer",
    "baseline_scores",
    "extract_commit_features",
    "extract_range_features",
    "features_from_file_changes",
    "score_change",
]

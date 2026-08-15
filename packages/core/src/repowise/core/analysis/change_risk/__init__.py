"""Just-in-time change-risk scoring.

Scores a *change* (a commit or a ``base..head`` range) for defect risk from its
diff shape — size, diffusion, authorship — using a linear, interpretable model
with offline-calibrated constants. Complements the file-level health score: it
is not size-dominated, so it flags risky *small* changes the file delta misses,
and is a natural pre-merge / PR gate.
"""

from __future__ import annotations

from .baseline import baseline_samples, scores_excluding
from .features import (
    WORKING_TREE_REF,
    ChangeFeatures,
    change_features_from_stored,
    extract_commit_features,
    extract_range_features,
    extract_worktree_features,
    features_from_file_changes,
    working_tree_is_dirty,
)
from .model import SCORE_UNIT, ChangeRisk, RiskDriver, score_change
from .normalize import RiskNormalizer, review_priority_classification
from .service import (
    ChangeRiskResult,
    change_risk_payload,
    normalize_extensions,
    range_anchor,
    riskignore_patterns,
    score_live_change,
)

__all__ = [
    "SCORE_UNIT",
    "WORKING_TREE_REF",
    "ChangeFeatures",
    "ChangeRisk",
    "ChangeRiskResult",
    "RiskDriver",
    "RiskNormalizer",
    "baseline_samples",
    "change_features_from_stored",
    "change_risk_payload",
    "extract_commit_features",
    "extract_range_features",
    "extract_worktree_features",
    "features_from_file_changes",
    "normalize_extensions",
    "range_anchor",
    "review_priority_classification",
    "riskignore_patterns",
    "score_change",
    "score_live_change",
    "scores_excluding",
    "working_tree_is_dirty",
]

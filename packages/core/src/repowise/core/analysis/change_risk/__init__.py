"""Just-in-time change-risk scoring.

Assesses a *change* (a commit or a ``base..head`` range) from its diff shape —
size, diffusion, and authorship — using a linear, interpretable model with
offline-calibrated constants. The repo-relative percentile/classification is
the review authority; the 0-10 model output is a supporting diff-shape signal,
not a probability. Complements the indexed file-health score.
"""

from __future__ import annotations

from .baseline import BaselineSample, baseline_samples, densities_excluding, scores_excluding
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
from .fix_history import (
    FixHistoryUnavailableError,
    change_fix_density,
    clear_fix_pressure_cache,
    fix_density_percentile,
    fix_pressure,
    hot_files,
)
from .model import SCORE_MEASURES, SCORE_UNIT, ChangeRisk, RiskDriver, score_change
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
    "SCORE_MEASURES",
    "SCORE_UNIT",
    "WORKING_TREE_REF",
    "BaselineSample",
    "ChangeFeatures",
    "ChangeRisk",
    "ChangeRiskResult",
    "FixHistoryUnavailableError",
    "RiskDriver",
    "RiskNormalizer",
    "baseline_samples",
    "change_features_from_stored",
    "change_fix_density",
    "change_risk_payload",
    "clear_fix_pressure_cache",
    "densities_excluding",
    "extract_commit_features",
    "extract_range_features",
    "extract_worktree_features",
    "features_from_file_changes",
    "fix_density_percentile",
    "fix_pressure",
    "hot_files",
    "normalize_extensions",
    "range_anchor",
    "review_priority_classification",
    "riskignore_patterns",
    "score_change",
    "score_live_change",
    "scores_excluding",
    "working_tree_is_dirty",
]

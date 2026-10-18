"""The better/worse story of commits and changes, read from stored health data.

Pure functions only: :mod:`.delta` reads one commit's stored delta,
:mod:`.rollup` sums commits into a change and changes into a period, and
:mod:`.wins` names files whose score rose. Nothing here re-runs the health
comparison; it belongs to :mod:`repowise.core.analysis.change_health`.
"""

from .delta import (
    DELTA_FILTERS,
    MIN_SHOWN_ANALYZER_VERSION,
    Currency,
    DeltaKind,
    DeltaStamp,
    FileScoreShift,
    HealthDeltaSummary,
    delta_currency,
    delta_kind,
    matches_delta,
    summarize_delta,
)
from .rollup import HealthRollup, PeriodHealthSummary, rollup_deltas, summarize_period
from .wins import MIN_GAIN, FileGain, parse_score_map, rising_files

__all__ = [
    "DELTA_FILTERS",
    "MIN_GAIN",
    "MIN_SHOWN_ANALYZER_VERSION",
    "Currency",
    "DeltaKind",
    "DeltaStamp",
    "FileGain",
    "FileScoreShift",
    "HealthDeltaSummary",
    "HealthRollup",
    "PeriodHealthSummary",
    "delta_currency",
    "delta_kind",
    "matches_delta",
    "parse_score_map",
    "rising_files",
    "rollup_deltas",
    "summarize_delta",
    "summarize_period",
]

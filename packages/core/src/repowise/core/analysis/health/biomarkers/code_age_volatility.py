"""Code Age Volatility — dormant code that's suddenly being modified.

Stable code that has sat largely untouched for a year and is now being
edited is one of the strongest defect predictors in the literature:
the editor is usually working in unfamiliar territory, the original
context has dimmed, and the code's earlier shape was tuned for
constraints that nobody remembers.

We derive both signals from the per-line :class:`BlameIndex` produced
by the FULL git tier:

* ``median_age_days`` — median ``now - author_time`` over the function's
  line range.
* ``recent_mod_count`` — distinct shas touching the range whose
  ``author_time`` falls inside the last 30 days.

Tier-aware: when ``ctx.blame_index`` is ``None`` (ESSENTIAL git tier),
or empty, the detector emits zero findings. Function-level no-op is the
documented "no signal" outcome until ``backfill_blame()`` runs.

Severity (calibrated against the 1-year / 2-year age boundaries):

* MEDIUM   ``median_age >= 365d`` and ``recent_mod_count >= 2``.
* HIGH     ``median_age >= 730d`` OR ``recent_mod_count >= 5`` (with
  the MEDIUM gate still met).
* CRITICAL ``median_age >= 730d`` AND ``recent_mod_count >= 5``.
"""

from __future__ import annotations

import time

from ....ingestion.git_indexer.function_blame import (
    median_author_time_in_range,
    recent_commits_in_range,
)
from ..models import Severity
from .base import BiomarkerResult, FileContext

_MEDIUM_AGE_DAYS = 365
_HIGH_AGE_DAYS = 730
_MEDIUM_RECENT = 2
_HIGH_RECENT = 5
_RECENT_WINDOW_SECS = 30 * 86400


def _severity_for(median_age_days: int, recent_mod_count: int) -> Severity:
    if median_age_days >= _HIGH_AGE_DAYS and recent_mod_count >= _HIGH_RECENT:
        return Severity.CRITICAL
    if median_age_days >= _HIGH_AGE_DAYS or recent_mod_count >= _HIGH_RECENT:
        return Severity.HIGH
    return Severity.MEDIUM


class CodeAgeVolatilityDetector:
    name = "code_age_volatility"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        idx = ctx.blame_index
        if idx is None or not idx.lines:
            return []

        now = int(time.time())
        since = now - _RECENT_WINDOW_SECS

        findings: list[BiomarkerResult] = []
        for fn_name, fc in ctx.function_metrics.items():
            median_ts = median_author_time_in_range(idx, fc.start_line, fc.end_line)
            if median_ts is None:
                continue
            median_age_days = max(0, (now - median_ts) // 86400)
            if median_age_days < _MEDIUM_AGE_DAYS:
                continue
            recent_mod = len(
                recent_commits_in_range(idx, fc.start_line, fc.end_line, since_unix_ts=since)
            )
            if recent_mod < _MEDIUM_RECENT:
                continue
            findings.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=_severity_for(median_age_days, recent_mod),
                    function_name=fn_name,
                    line_start=fc.start_line,
                    line_end=fc.end_line,
                    details={
                        "median_age_days": int(median_age_days),
                        "recent_mod_count": int(recent_mod),
                    },
                    reason=(
                        f"{fn_name} has a median line age of "
                        f"{int(median_age_days)} days but has been "
                        f"modified by {recent_mod} commits in the last 30 days"
                    ),
                )
            )
        return findings


BIOMARKER = CodeAgeVolatilityDetector()

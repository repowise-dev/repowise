"""Reconstruct per-category score breakdowns from stored findings.

The reconstruction lives in ``repowise.core.analysis.health.aggregation``; this
keeps the private import path the routes and tests already use.
"""

from __future__ import annotations

from repowise.core.analysis.health.aggregation import (
    finding_base_deduction as _finding_base_deduction,
)
from repowise.core.analysis.health.aggregation import (
    score_breakdown as _score_breakdown_from_findings,
)

__all__ = ["_finding_base_deduction", "_score_breakdown_from_findings"]

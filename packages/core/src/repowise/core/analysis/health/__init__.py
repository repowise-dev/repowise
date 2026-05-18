"""Code Health analysis layer.

Public surface kept minimal. Engine + report types only — sub-packages
are accessed directly by their owners (pipeline orchestrator, MCP tool,
tests).
"""

from __future__ import annotations

from .engine import HealthAnalyzer
from .models import (
    HealthFileMetricData,
    HealthFindingData,
    HealthReport,
    Severity,
)

__all__ = [
    "HealthAnalyzer",
    "HealthFileMetricData",
    "HealthFindingData",
    "HealthReport",
    "Severity",
]

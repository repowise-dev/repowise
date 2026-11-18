"""Code Health analysis layer.

Public surface kept minimal. Engine + report types only — sub-packages
are accessed directly by their owners (pipeline orchestrator, MCP tool,
tests).

The engine is bound on first attribute access rather than at import. Importing
it eagerly loaded ingestion, git blame and, through them, persistence — for
*any* import under this package, including the record adapters in ``rows`` and
the pure folds in ``ranking``, ``aggregation`` and ``trends``. Persistence
importing one of those back then closed a real cycle, which is why the crud
layer had to defer its own import of ``rows``. ``from ... import
HealthAnalyzer`` still works and still loads the engine; only the cost of *not*
asking for it went away.

Half the job: ``scoring`` keeps its one biomarker import behind ``TYPE_CHECKING``
for the same reason. Undoing either brings the whole tail back, and
``tests/unit/health/test_lightweight_health_imports.py`` fails if one is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import (
    HealthFileMetricData,
    HealthFindingData,
    HealthReport,
    Severity,
)

if TYPE_CHECKING:
    from .engine import HEALTH_ANALYZER_VERSION, HealthAnalyzer

_ENGINE_EXPORTS = frozenset({"HEALTH_ANALYZER_VERSION", "HealthAnalyzer"})

__all__ = [
    "HEALTH_ANALYZER_VERSION",
    "HealthAnalyzer",
    "HealthFileMetricData",
    "HealthFindingData",
    "HealthReport",
    "Severity",
    "health_analyzer_changed",
]


def health_analyzer_changed(state: dict[str, Any]) -> bool:
    """Whether the stored health rows were written by a different analyzer.

    *state* is the repo's ``state.json``. A legacy state file with no stamp is
    **not** a change: the rows it wrote are no more suspect than any other
    pre-upgrade rows, and treating absence as drift would re-score every
    existing install once on upgrade for no stated defect. They pick the stamp
    up on their first re-score from any other trigger.
    """
    stored = state.get("health_analyzer_version")
    if stored is None:
        return False
    from .engine import HEALTH_ANALYZER_VERSION

    return stored != HEALTH_ANALYZER_VERSION


def __getattr__(name: str) -> Any:
    if name in _ENGINE_EXPORTS:
        from . import engine

        return getattr(engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Everything really bound, plus the two names that bind on access."""
    return sorted(set(globals()) | _ENGINE_EXPORTS)

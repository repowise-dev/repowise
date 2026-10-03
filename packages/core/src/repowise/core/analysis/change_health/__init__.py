"""Base-versus-head code-health comparison.

Answers "what did this change newly make worse", for a commit, a range, or the
uncommitted tree. Both sides are analysed from their own content through a
:class:`RevisionSource`, so nothing here assumes a local checkout and a hosted
adapter can be added without touching matching or attribution.

The pipeline, in order::

    RevisionSource -> RevisionHealthAnalyzer -> FindingMatcher
                   -> FindingAttributor -> ChangeHealthDeltaService
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .sources import (
    FileChange,
    GitRevisionSource,
    MappingRevisionSource,
    RevisionPair,
    RevisionSource,
    filter_changes,
)

if TYPE_CHECKING:
    from .analyzer import RevisionAnalysis, RevisionHealthAnalyzer
    from .attribution import Attribution, FindingAttributor
    from .identity import change_finding_id, finding_key
    from .matcher import FindingMatcher, MatchedFinding, MatchResult
    from .models import (
        AnalysisFingerprint,
        AttributionBasis,
        ChangeFinding,
        ChangeHealthDelta,
        ChangeKind,
        DeltaStatus,
        FindingKey,
        RevisionId,
        ScopeCounts,
    )
    from .perf_delta import PerfOpportunityView, opportunities_for
    from .service import ChangeHealthDeltaService, DeltaRequest

# Bound on first access: the analyzer loads the whole health engine (~0.6s),
# which a caller wanting only the diff shape in ``sources`` should not pay.
_LAZY_EXPORTS: dict[str, str] = {
    "RevisionAnalysis": "analyzer",
    "RevisionHealthAnalyzer": "analyzer",
    "Attribution": "attribution",
    "FindingAttributor": "attribution",
    "change_finding_id": "identity",
    "finding_key": "identity",
    "FindingMatcher": "matcher",
    "MatchedFinding": "matcher",
    "MatchResult": "matcher",
    "AnalysisFingerprint": "models",
    "AttributionBasis": "models",
    "ChangeFinding": "models",
    "ChangeHealthDelta": "models",
    "ChangeKind": "models",
    "DeltaStatus": "models",
    "FindingKey": "models",
    "RevisionId": "models",
    "ScopeCounts": "models",
    "PerfOpportunityView": "perf_delta",
    "opportunities_for": "perf_delta",
    "ChangeHealthDeltaService": "service",
    "DeltaRequest": "service",
}

__all__ = [
    "AnalysisFingerprint",
    "Attribution",
    "AttributionBasis",
    "ChangeFinding",
    "ChangeHealthDelta",
    "ChangeHealthDeltaService",
    "ChangeKind",
    "DeltaRequest",
    "DeltaStatus",
    "FileChange",
    "FindingAttributor",
    "FindingKey",
    "FindingMatcher",
    "GitRevisionSource",
    "MappingRevisionSource",
    "MatchResult",
    "MatchedFinding",
    "PerfOpportunityView",
    "RevisionAnalysis",
    "RevisionHealthAnalyzer",
    "RevisionId",
    "RevisionPair",
    "RevisionSource",
    "ScopeCounts",
    "change_finding_id",
    "filter_changes",
    "finding_key",
    "opportunities_for",
]


def __getattr__(name: str) -> Any:
    if (module := _LAZY_EXPORTS.get(name)) is not None:
        from importlib import import_module

        return getattr(import_module(f"{__name__}.{module}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


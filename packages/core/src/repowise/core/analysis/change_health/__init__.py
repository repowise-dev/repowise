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
from .sources import FileChange, GitRevisionSource, RevisionPair, RevisionSource

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
    "finding_key",
    "opportunities_for",
]

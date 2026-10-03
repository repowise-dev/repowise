"""Documentation drift detection.

Documents make assertions about the repository. This package checks the ones
that are checkable, and is explicit about the ones that are not.

Five reference classes ship: ``path``, ``link``, ``anchor``, ``command`` and
``symbol``. The last needs an index and git history; see
:class:`~.models.DriftKind`.
"""

from .analyzer import DocDriftAnalyzer, summarize_confidence
from .models import (
    DocDriftFindingData,
    DocDriftReport,
    DocReference,
    DriftKind,
    DriftVerdict,
    ResolvedDocReference,
)

__all__ = [
    "DocDriftAnalyzer",
    "DocDriftFindingData",
    "DocDriftReport",
    "DocReference",
    "DriftKind",
    "DriftVerdict",
    "ResolvedDocReference",
    "summarize_confidence",
]

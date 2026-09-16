"""Documentation drift detection.

Documents make assertions about the repository. This package checks the ones
that are checkable, and is explicit about the ones that are not.

Four reference classes ship: ``path``, ``link``, ``anchor`` and ``command``.
A fifth, ``symbol``, was measured and rejected --- see
:class:`~.models.DriftKind`.
"""

from .analyzer import DocDriftAnalyzer, summarize_confidence
from .models import (
    DocDriftFindingData,
    DocDriftReport,
    DocReference,
    DriftKind,
    DriftVerdict,
)

__all__ = [
    "DocDriftAnalyzer",
    "DocDriftFindingData",
    "DocDriftReport",
    "DocReference",
    "DriftKind",
    "DriftVerdict",
    "summarize_confidence",
]

"""Documentation drift detection.

Documents make assertions about the repository. This package checks the ones
that are checkable, and is explicit about the ones that are not.

Five reference classes ship: ``path``, ``link``, ``anchor``, ``command`` and
``symbol``. The last needs an index and git history; see
:class:`~.models.DriftKind`.

The analyzer is bound on first attribute access rather than at import. It pulls
in the extractor, resolver, symbol pass and structlog, and a leaf import such as
``doc_drift.serialize`` or ``doc_drift.constants`` initialises this package
first, so every reader of those small modules paid for the whole pass.
``from ... import DocDriftAnalyzer`` still works and still loads it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...lazy_exports import lazy_exports
from .models import (
    DocDriftFindingData,
    DocDriftReport,
    DocReference,
    DriftKind,
    DriftVerdict,
    ResolvedDocReference,
)

if TYPE_CHECKING:
    from .analyzer import DocDriftAnalyzer, summarize_confidence

__getattr__, __dir__ = lazy_exports(
    __name__,
    {"DocDriftAnalyzer": ".analyzer", "summarize_confidence": ".analyzer"},
    globals(),
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

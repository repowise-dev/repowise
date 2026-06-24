"""Refactoring-detector registry — the modular spine of the layer.

Every refactoring type is a self-contained ``RefactoringDetector`` that
registers itself with the ``@register`` decorator. Adding a refactoring is a
new file plus an import in ``__init__.py`` — zero edits to the health engine.
``detect_refactorings`` runs every registered detector with per-detector
fault isolation (a detector that raises yields no suggestion, never breaks
the health pass), mirroring the biomarker registry's ``detect_all``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import structlog

from .models import RefactoringContext, RefactoringSuggestion

log = structlog.get_logger(__name__)


def effort_bucket(nloc: int) -> str:
    """Map a target's NLOC to a coarse effort bucket.

    Shared by every detector so the effort label is consistent across
    refactoring types (matches the CLI's refactoring-targets thresholds).
    """
    if nloc <= 40:
        return "S"
    if nloc <= 150:
        return "M"
    if nloc <= 400:
        return "L"
    return "XL"


class RefactoringDetector(ABC):
    """Detector contract. Each concrete detector sets a unique ``name`` and
    implements ``detect`` over a single file's ``RefactoringContext``.
    """

    name: str = ""

    @abstractmethod
    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        """Return zero or more suggestions for *ctx*. Deterministic: the same
        context must always yield the same suggestions in the same order."""
        raise NotImplementedError


# Registration is import-time and order-deterministic: ``__init__`` imports
# the detector modules in a fixed order, so ``_REGISTRY`` is stable per run.
_REGISTRY: list[RefactoringDetector] = []


def register(cls: type[RefactoringDetector]) -> type[RefactoringDetector]:
    """Class decorator: instantiate *cls* and add it to the registry."""
    _REGISTRY.append(cls())
    return cls


def registered_detectors(*, disabled: Sequence[str] = ()) -> list[RefactoringDetector]:
    """Active detector instances, minus any whose ``name`` is *disabled*."""
    disabled_set = set(disabled)
    return [d for d in _REGISTRY if d.name not in disabled_set]


def detect_refactorings(
    ctx: RefactoringContext, *, disabled: Sequence[str] = ()
) -> list[RefactoringSuggestion]:
    """Run every registered detector on *ctx*, fault-isolated per detector."""
    out: list[RefactoringSuggestion] = []
    for detector in registered_detectors(disabled=disabled):
        try:
            out.extend(detector.detect(ctx))
        except Exception as exc:
            # One bad detector must not break the health pass; degrade to
            # "no suggestion" for this file.
            log.debug("refactoring_detector_failed", detector=detector.name, error=str(exc))
    return out

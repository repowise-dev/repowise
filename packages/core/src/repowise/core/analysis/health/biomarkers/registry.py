"""Biomarker registry.

Explicit factory list rather than module auto-discovery — keeps the
registration order deterministic and lets tests inject custom detectors
via ``registered_biomarkers(extra=...)``.

Adding a biomarker: append to ``_DETECTOR_FACTORIES``.
"""

from __future__ import annotations

from collections.abc import Sequence

from .base import Biomarker, BiomarkerResult, FileContext
from .brain_method import BrainMethodDetector
from .complex_method import ComplexMethodDetector
from .coverage_gap import CoverageGapDetector
from .nested_complexity import NestedComplexityDetector
from .untested_hotspot import UntestedHotspotDetector

_DETECTOR_FACTORIES: list[type[Biomarker]] = [
    BrainMethodDetector,  # type: ignore[list-item]
    NestedComplexityDetector,  # type: ignore[list-item]
    ComplexMethodDetector,  # type: ignore[list-item]
    UntestedHotspotDetector,  # type: ignore[list-item]
    CoverageGapDetector,  # type: ignore[list-item]
]


def registered_biomarkers(
    *, disabled: Sequence[str] = (), extra: Sequence[Biomarker] = ()
) -> list[Biomarker]:
    """Return the active biomarker list.

    Phase 3 will read ``disabled`` from ``.repowise/health-rules.json``.
    """
    instances: list[Biomarker] = [cls() for cls in _DETECTOR_FACTORIES]  # type: ignore[call-arg]
    instances.extend(extra)
    return [b for b in instances if b.name not in disabled]


def detect_all(
    ctx: FileContext,
    *,
    disabled: Sequence[str] = (),
    extra: Sequence[Biomarker] = (),
) -> list[BiomarkerResult]:
    """Run every registered biomarker against *ctx* and return the union."""
    findings: list[BiomarkerResult] = []
    for b in registered_biomarkers(disabled=disabled, extra=extra):
        try:
            findings.extend(b.detect(ctx))
        except Exception:
            continue
    return findings

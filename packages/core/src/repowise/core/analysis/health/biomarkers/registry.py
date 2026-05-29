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
from .bumpy_road import BumpyRoadDetector
from .change_entropy import ChangeEntropyDetector
from .churn_risk import ChurnRiskDetector
from .co_change_scatter import CoChangeScatterDetector
from .code_age_volatility import CodeAgeVolatilityDetector
from .complex_conditional import ComplexConditionalDetector
from .complex_method import ComplexMethodDetector
from .coverage_gap import CoverageGapDetector
from .developer_congestion import DeveloperCongestionDetector
from .dry_violation import DryViolationDetector
from .duplicated_assertion_block import DuplicatedAssertionBlockDetector
from .function_hotspot import FunctionHotspotDetector
from .god_class import GodClassDetector
from .hidden_coupling import HiddenCouplingDetector
from .knowledge_loss import KnowledgeLossDetector
from .large_assertion_block import LargeAssertionBlockDetector
from .large_method import LargeMethodDetector
from .low_cohesion import LowCohesionDetector
from .nested_complexity import NestedComplexityDetector
from .ownership_risk import OwnershipRiskDetector
from .primitive_obsession import PrimitiveObsessionDetector
from .untested_hotspot import UntestedHotspotDetector

_DETECTOR_FACTORIES: list[type[Biomarker]] = [
    BrainMethodDetector,  # type: ignore[list-item]
    LowCohesionDetector,  # type: ignore[list-item]
    GodClassDetector,  # type: ignore[list-item]
    NestedComplexityDetector,  # type: ignore[list-item]
    ComplexMethodDetector,  # type: ignore[list-item]
    BumpyRoadDetector,  # type: ignore[list-item]
    LargeMethodDetector,  # type: ignore[list-item]
    PrimitiveObsessionDetector,  # type: ignore[list-item]
    DryViolationDetector,  # type: ignore[list-item]
    UntestedHotspotDetector,  # type: ignore[list-item]
    CoverageGapDetector,  # type: ignore[list-item]
    DeveloperCongestionDetector,  # type: ignore[list-item]
    KnowledgeLossDetector,  # type: ignore[list-item]
    HiddenCouplingDetector,  # type: ignore[list-item]
    ComplexConditionalDetector,  # type: ignore[list-item]
    FunctionHotspotDetector,  # type: ignore[list-item]
    CodeAgeVolatilityDetector,  # type: ignore[list-item]
    OwnershipRiskDetector,  # type: ignore[list-item]
    ChurnRiskDetector,  # type: ignore[list-item]
    ChangeEntropyDetector,  # type: ignore[list-item]
    CoChangeScatterDetector,  # type: ignore[list-item]
    LargeAssertionBlockDetector,  # type: ignore[list-item]
    DuplicatedAssertionBlockDetector,  # type: ignore[list-item]
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

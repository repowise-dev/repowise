"""Biomarker registry.

Explicit factory list rather than module auto-discovery — keeps the
registration order deterministic and lets tests inject custom detectors
via ``registered_biomarkers(extra=...)``.

Adding a biomarker: append to ``_DETECTOR_FACTORIES``.
"""

from __future__ import annotations

from collections.abc import Sequence

from .array_spread_in_reduce import ArraySpreadInReduceDetector
from .base import Biomarker, BiomarkerResult, FileContext
from .blocking_io_under_lock import BlockingIoUnderLockDetector
from .blocking_sync_in_async import BlockingSyncInAsyncDetector
from .brain_method import BrainMethodDetector
from .bumpy_road import BumpyRoadDetector
from .change_entropy import ChangeEntropyDetector
from .churn_risk import ChurnRiskDetector
from .co_change_scatter import CoChangeScatterDetector
from .code_age_volatility import CodeAgeVolatilityDetector
from .complex_conditional import ComplexConditionalDetector
from .complex_method import ComplexMethodDetector
from .coverage_gap import CoverageGapDetector
from .coverage_gradient import CoverageGradientDetector
from .defer_in_loop import DeferInLoopDetector
from .developer_congestion import DeveloperCongestionDetector
from .dry_violation import DryViolationDetector
from .duplicated_assertion_block import DuplicatedAssertionBlockDetector
from .error_handling import ErrorHandlingDetector
from .function_hotspot import FunctionHotspotDetector
from .god_class import GodClassDetector
from .goroutine_in_unbounded_loop import GoroutineInUnboundedLoopDetector
from .hidden_coupling import HiddenCouplingDetector
from .hot_path_sync_io import HotPathSyncIoDetector
from .io_in_loop import IoInLoopDetector
from .json_parse_in_loop import JsonParseInLoopDetector
from .knowledge_loss import KnowledgeLossDetector
from .large_assertion_block import LargeAssertionBlockDetector
from .large_method import LargeMethodDetector
from .list_insert_zero_in_loop import ListInsertZeroInLoopDetector
from .lock_in_loop import LockInLoopDetector
from .low_cohesion import LowCohesionDetector
from .membership_test_against_list_in_loop import MembershipTestAgainstListInLoopDetector
from .nested_complexity import NestedComplexityDetector
from .nested_loop_quadratic import NestedLoopQuadraticDetector
from .nested_loop_with_io import NestedLoopWithIoDetector
from .ownership_risk import OwnershipRiskDetector
from .pandas_iterrows_in_loop import PandasIterrowsInLoopDetector
from .pd_concat_in_loop import PdConcatInLoopDetector
from .primitive_obsession import PrimitiveObsessionDetector
from .prior_defect import PriorDefectDetector
from .regex_compile_in_loop import RegexCompileInLoopDetector
from .resource_construction_in_loop import ResourceConstructionInLoopDetector
from .serial_await_in_loop import SerialAwaitInLoopDetector
from .sql_cartesian_join import SqlCartesianJoinDetector
from .sql_high_complexity import SqlHighComplexityDetector
from .sql_select_star import SqlSelectStarDetector
from .sql_update_delete_without_where import SqlUpdateDeleteWithoutWhereDetector
from .string_concat_in_loop import StringConcatInLoopDetector
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
    CoverageGradientDetector,  # type: ignore[list-item]
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
    PriorDefectDetector,  # type: ignore[list-item]
    LargeAssertionBlockDetector,  # type: ignore[list-item]
    DuplicatedAssertionBlockDetector,  # type: ignore[list-item]
    ErrorHandlingDetector,  # type: ignore[list-item]
    # Performance dimension (advisory weight; bounded by the perf cap).
    IoInLoopDetector,  # type: ignore[list-item]
    StringConcatInLoopDetector,  # type: ignore[list-item]
    BlockingSyncInAsyncDetector,  # type: ignore[list-item]
    # Phase 6 dialect markers — emitted by the Java/Go/Rust dialects but
    # previously unwired (no lifter), so recall-zero until now.
    RegexCompileInLoopDetector,  # type: ignore[list-item]
    DeferInLoopDetector,  # type: ignore[list-item]
    # Phase 7a — cheap, high-precision loop markers.
    ResourceConstructionInLoopDetector,  # type: ignore[list-item]
    LockInLoopDetector,  # type: ignore[list-item]
    SerialAwaitInLoopDetector,  # type: ignore[list-item]
    MembershipTestAgainstListInLoopDetector,  # type: ignore[list-item]
    # Phase 7b — centrality-gated moat markers.
    NestedLoopWithIoDetector,  # type: ignore[list-item]
    NestedLoopQuadraticDetector,  # type: ignore[list-item]
    HotPathSyncIoDetector,  # type: ignore[list-item]
    BlockingIoUnderLockDetector,  # type: ignore[list-item]
    # Phase 7d — language-specific markers (advisory weight; bounded by the cap).
    ListInsertZeroInLoopDetector,  # type: ignore[list-item]
    PdConcatInLoopDetector,  # type: ignore[list-item]
    PandasIterrowsInLoopDetector,  # type: ignore[list-item]
    JsonParseInLoopDetector,  # type: ignore[list-item]
    ArraySpreadInReduceDetector,  # type: ignore[list-item]
    GoroutineInUnboundedLoopDetector,  # type: ignore[list-item]
    # SQL markers (from the sqlglot-backed SQL walker; maintainability and
    # performance only, never the defect headline).
    SqlHighComplexityDetector,  # type: ignore[list-item]
    SqlSelectStarDetector,  # type: ignore[list-item]
    SqlUpdateDeleteWithoutWhereDetector,  # type: ignore[list-item]
    SqlCartesianJoinDetector,  # type: ignore[list-item]
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

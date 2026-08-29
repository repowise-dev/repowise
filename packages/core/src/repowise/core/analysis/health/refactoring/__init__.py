"""Deterministic refactoring-intelligence layer (code-health sub-capability).

Turns the structural signals the health pass already computes (LCOM4
cohesion components, clone pairs, the call graph) into concrete, structured
``RefactoringSuggestion`` plans — "split this class into these groups",
"extract this clone here" — with zero LLM calls and zero new runtime deps.

Importing this package registers every detector (the modules below
self-register via ``@register``), so ``detect_refactorings`` sees them.
"""

from __future__ import annotations

# Importing the detector modules triggers their ``@register`` side effect.
# Listed explicitly (and in a fixed order) so the registry is deterministic.
from . import (
    break_cycle,  # noqa: F401  (import-for-side-effect)
    extract_class,  # noqa: F401  (import-for-side-effect)
    extract_helper,  # noqa: F401  (import-for-side-effect)
    extract_method,  # noqa: F401  (import-for-side-effect)
    move_method,  # noqa: F401  (import-for-side-effect)
    split_file,  # noqa: F401  (import-for-side-effect)
)
from .graph_signals import build_file_scc_index
from .identity import (
    REFACTORING_MODEL_VERSION,
    assign_public_ids,
    model_state,
    refactoring_kernel,
    refactoring_public_id,
)
from .models import (
    CONFIDENCE_LEVELS,
    RefactoringContext,
    RefactoringSuggestion,
)
from .performance_fix import PerformancePlanPolicy, performance_fix_suggestions
from .rank import rank_suggestions
from .registry import (
    RefactoringDetector,
    detect_refactorings,
    effort_bucket,
    register,
    registered_detectors,
)

__all__ = [
    "CONFIDENCE_LEVELS",
    "REFACTORING_MODEL_VERSION",
    "PerformancePlanPolicy",
    "RefactoringContext",
    "RefactoringDetector",
    "RefactoringSuggestion",
    "assign_public_ids",
    "build_file_scc_index",
    "detect_refactorings",
    "effort_bucket",
    "model_state",
    "performance_fix_suggestions",
    "rank_suggestions",
    "refactoring_kernel",
    "refactoring_public_id",
    "register",
    "registered_detectors",
]

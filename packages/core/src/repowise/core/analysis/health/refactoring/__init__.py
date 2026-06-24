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
    extract_class,  # noqa: F401  (import-for-side-effect)
    extract_helper,  # noqa: F401  (import-for-side-effect)
)
from .models import (
    CONFIDENCE_LEVELS,
    RefactoringContext,
    RefactoringSuggestion,
)
from .registry import (
    RefactoringDetector,
    detect_refactorings,
    effort_bucket,
    register,
    registered_detectors,
)

__all__ = [
    "CONFIDENCE_LEVELS",
    "RefactoringContext",
    "RefactoringDetector",
    "RefactoringSuggestion",
    "detect_refactorings",
    "effort_bucket",
    "register",
    "registered_detectors",
]

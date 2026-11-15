"""Tree-sitter complexity walker — see ``README.md``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....lazy_exports import lazy_exports

if TYPE_CHECKING:
    from .walker import (
        ClassComplexity,
        CohesionGroup,
        ConditionComplexity,
        ErrorHandlingHit,
        FileComplexity,
        FunctionComplexity,
        PerfFnFacts,
        PerfHit,
        walk_file,
        walk_file_complexity,
    )

__all__ = [
    "ClassComplexity",
    "CohesionGroup",
    "ConditionComplexity",
    "ErrorHandlingHit",
    "FileComplexity",
    "FunctionComplexity",
    "PerfFnFacts",
    "PerfHit",
    "walk_file",
    "walk_file_complexity",
]

# The walker loads every grammar and extractor (~0.3 s); a leaf such as
# ``test_case``, read by the symbol and context tools, needs none of it.
__getattr__, __dir__ = lazy_exports(__name__, dict.fromkeys(__all__, ".walker"), globals())

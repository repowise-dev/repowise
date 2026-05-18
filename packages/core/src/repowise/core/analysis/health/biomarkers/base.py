"""Biomarker contract: Protocol + ``BiomarkerResult`` + ``FileContext``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..complexity import FunctionComplexity
from ..models import Severity


@dataclass
class FileContext:
    """All inputs a biomarker may need to evaluate a file.

    Populated by ``engine.py`` once per file. Biomarkers are pure
    functions over this context — they don't open files or talk to the
    DB themselves.
    """

    file_path: str
    language: str
    nloc: int
    has_test_file: bool
    module: str | None
    # Map symbol-name → complexity metrics for functions/methods in this
    # file. Symbols without a complexity row default to CCN=1, nesting=0.
    function_metrics: dict[str, FunctionComplexity] = field(default_factory=dict)
    # Per-file git metadata (may be empty when git indexing skipped).
    git_meta: dict[str, Any] = field(default_factory=dict)
    # Graph-derived signals.
    dependents_count: int = 0
    pagerank_score: float = 0.0


@dataclass
class BiomarkerResult:
    """One biomarker hit before scoring deductions are applied."""

    biomarker_type: str
    severity: Severity
    function_name: str | None
    line_start: int | None
    line_end: int | None
    details: dict[str, Any]
    reason: str = ""


class Biomarker(Protocol):
    """Detector contract. Each concrete biomarker is a stateless object."""

    name: str
    category: str  # one of the scoring categories in ``scoring.CATEGORY_CAPS``.

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]: ...

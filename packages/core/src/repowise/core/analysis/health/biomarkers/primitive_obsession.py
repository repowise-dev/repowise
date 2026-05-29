"""Primitive Obsession — long parameter lists of unstructured values.

A proxy for the OOP smell: when a function signature carries 5+ raw
parameters, the call sites usually pass strings/ints/bools that *should*
be a value object. We don't try to inspect parameter *types* (too much
language-specific machinery) — the raw count is a strong-enough
correlation in practice and matches CodeScene's "Primitive Obsession"
default.

Constructors (`__init__`, `init`) get a small grace allowance — wide
dataclass-style ctors are an idiomatic pattern, not a smell.

A wide signature only counts as obsession in a file with enough substance to
*have* a design (`_MIN_FILE_NLOC`). In a tiny module a long parameter list is
almost always an idiomatic config/builder/forwarder entry point rather than a
value object that wants extracting — and an empirical defect-prediction analysis
across a 13-repo corpus confirmed the firing is anti-predictive there (it flags
clean small utility files), inverting discrimination on the small-file size
band. The floor keeps the smell where it carries signal without touching its
scoring weight; larger modules and the per-function param logic are unchanged.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_PARAM_THRESHOLD = 5
_CTOR_GRACE = 2  # constructors get +2 free params before the smell trips
_CTOR_NAMES = frozenset({"__init__", "init", "constructor"})
# A wide signature in a module below this many non-blank lines is idiomatic
# (config/builder/DTO), not a design smell — empirically anti-predictive of
# defects on small files. Tuned on the defect benchmark's small-file size band.
_MIN_FILE_NLOC = 60


class PrimitiveObsessionDetector:
    name = "primitive_obsession"
    category = "size_and_complexity"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.nloc < _MIN_FILE_NLOC:
            return []
        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            threshold = _PARAM_THRESHOLD
            if fn.name in _CTOR_NAMES:
                threshold += _CTOR_GRACE
            if fn.param_count < threshold:
                continue
            severity = (
                Severity.HIGH
                if fn.param_count >= threshold + 4
                else Severity.MEDIUM
                if fn.param_count >= threshold + 2
                else Severity.LOW
            )
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={
                        "param_count": fn.param_count,
                    },
                    reason=f"{fn.name} takes {fn.param_count} parameters",
                )
            )
        return out


BIOMARKER = PrimitiveObsessionDetector()

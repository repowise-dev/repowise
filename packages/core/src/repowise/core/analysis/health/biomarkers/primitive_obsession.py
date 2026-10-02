"""Long parameter list (key ``primitive_obsession``) — wide signatures of raw values.

A proxy for the OOP smell: when a function signature carries 5+ raw
parameters, the call sites usually pass strings/ints/bools that *should*
be a value object. Where the signature declares types, the walker counts
how many are scalars or strings (``complexity/signature.py``).

Constructors get a small grace allowance — wide dataclass-style ctors are an
idiomatic pattern, not a smell. A constructor is known by its node kind or by
carrying its type's name, so a Java or C# constructor gets it too.

Two narrowings keep the finding to signatures someone can actually fix:

- A signature set by another declaration is skipped: an override, an
  interface or trait implementation, a native binding. Its parameters belong
  to the base method, the interface or the foreign ABI, and an SPI hook with
  nine parameters is not this method's smell.
- When the signature declares types, a majority of its parameters must be
  scalars or strings. A wide list of rich domain types is already the value
  objects this smell asks for. An untyped signature (unannotated Python or
  JavaScript) has no types to read and is judged on the count alone, as
  before.

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

from ..complexity import FunctionComplexity
from ..models import Severity
from .base import BiomarkerResult, FileContext

_PARAM_THRESHOLD = 5
_CTOR_GRACE = 2  # constructors get +2 free params before the smell trips
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
        for fn in ctx.all_functions:
            threshold = _threshold(fn)
            if threshold is None:
                continue
            primitive = fn.primitive_param_count
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=_severity(fn.param_count, threshold),
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={
                        "param_count": fn.param_count,
                        "primitive_param_count": primitive,
                    },
                    reason=(
                        f"long parameter list: {fn.name} takes {fn.param_count} parameters"
                        + (f", {primitive} of them scalars or strings" if primitive else "")
                    ),
                )
            )
        return out


def _threshold(fn: FunctionComplexity) -> int | None:
    """The parameter count *fn* is held to, or ``None`` when it does not qualify."""
    # A test case's parameters are fixtures the runner injects, not a
    # signature anyone calls.
    if fn.is_test_case or fn.signature_fixed:
        return None
    threshold = _PARAM_THRESHOLD + (_CTOR_GRACE if fn.is_constructor else 0)
    if fn.param_count < threshold:
        return None
    primitive = fn.primitive_param_count
    if primitive is not None and primitive * 2 <= fn.param_count:
        return None
    return threshold


def _severity(param_count: int, threshold: int) -> Severity:
    if param_count >= threshold + 4:
        return Severity.HIGH
    return Severity.MEDIUM if param_count >= threshold + 2 else Severity.LOW

BIOMARKER = PrimitiveObsessionDetector()

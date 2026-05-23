"""Complex Conditional — compound boolean expressions inside one branch.

Flags ``if`` / ``while`` / ``for`` / ternary / case guards whose
condition is glued together from three or more boolean operators
(``&&`` / ``||`` / ``and`` / ``or``). Three-operator conditions are
already harder to read at a glance than a single equality; six-operator
conditions are routinely cited in defect-mining studies as the noisy
end of a branch's lifetime.

Category: ``structural_complexity``.

Consumes ``FunctionComplexity.complex_conditions`` — collected by the
walker as a side-channel without affecting CCN/cognitive scores.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_LOW = 3
_MEDIUM = 4
_HIGH = 5
_CRITICAL = 6


def _severity_for(op_count: int) -> Severity:
    if op_count >= _CRITICAL:
        return Severity.CRITICAL
    if op_count >= _HIGH:
        return Severity.HIGH
    if op_count >= _MEDIUM:
        return Severity.MEDIUM
    return Severity.LOW


class ComplexConditionalDetector:
    name = "complex_conditional"
    category = "structural_complexity"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn_name, fc in ctx.function_metrics.items():
            for cond in fc.complex_conditions or []:
                if cond.operator_count < _LOW:
                    continue
                out.append(
                    BiomarkerResult(
                        biomarker_type=self.name,
                        severity=_severity_for(cond.operator_count),
                        function_name=fn_name,
                        line_start=cond.line,
                        line_end=cond.line,
                        details={
                            "operator_count": cond.operator_count,
                            "enclosing_construct": cond.enclosing_construct,
                        },
                        reason=(
                            f"{cond.enclosing_construct} condition combines "
                            f"{cond.operator_count} boolean operators"
                        ),
                    )
                )
        return out


BIOMARKER = ComplexConditionalDetector()

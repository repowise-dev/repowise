"""Error Handling — swallowed-exception / unsafe-unwrap anti-patterns.

Surfaces the error-handling smells every linter is expected to flag: an
empty ``catch`` / ``except: pass``, a Python catch-all ``except:``, a
Rust ``.unwrap()`` / ``panic!``, a Go error checked-then-ignored or
discarded via the blank identifier. One LOW finding per occurrence, each
anchored to its line.

This is a bounded maintainability signal, NOT a defect predictor: on the
21-repo / 9-language T0 benchmark it is AUC-neutral (OOF delta ~0, CI
crosses zero) but size-orthogonal and the least redundant signal tested
(max |rho| ~0.21 vs the calibrated roster; churn rho ~0.02). It therefore
ships in its own ``error_handling`` category with an advisory 0.5 cap and
a floored 0.5 weight, and is excluded from the defect-calibration roster.

Detection happens in the complexity walker's whole-tree pass (see
``complexity.error_handling._collect_error_handling``); this detector just lifts
the pre-collected hits into findings. Precision-first: unsupported
languages and parse failures yield zero hits, never a false positive.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_REASONS: dict[str, str] = {
    "swallowed_catch": "caught exception is swallowed without any handling",
    "bare_except": "catch-all except hides every error, including KeyboardInterrupt",
    "unsafe_unwrap": "unwrap/expect/panic turns a recoverable error into a crash",
    "go_swallow": "error value is checked then ignored, or discarded via the blank identifier",
}


class ErrorHandlingDetector:
    name = "error_handling"
    category = "error_handling"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.error_handling_hits:
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=None,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={"kind": hit.kind},
                    reason=_REASONS.get(hit.kind, hit.kind),
                )
            )
        return out


BIOMARKER = ErrorHandlingDetector()

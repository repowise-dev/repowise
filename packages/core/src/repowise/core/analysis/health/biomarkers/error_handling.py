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
``complexity.error_handling._eh_visit``); this detector just lifts
the pre-collected hits into findings. Precision-first: unsupported
languages and parse failures yield zero hits, never a false positive.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_REASONS: dict[str, str] = {
    "swallowed_catch": "caught exception is swallowed without any handling",
    "bare_except": "bare/BaseException catch also swallows KeyboardInterrupt and SystemExit",
    "broad_except": "broad `except Exception` catches unrelated errors and can hide bugs",
    "unsafe_unwrap": "unwrap/expect turns a recoverable error into a crash",
    "panic_macro": "panic!/unreachable!/todo!/unimplemented! aborts the process unconditionally",
    "go_swallow": "error value is checked then ignored, or discarded via the blank identifier",
}

# Rust invariant assertions (``complexity.rust_unwrap``): true, idiomatic, low
# priority. They keep their kind (and so their id) but say what they are and
# deduct nothing from the score.
_IDIOM_REASONS: dict[str, str] = {
    "lock_poison": (
        "unwrapping a lock panics only if another thread panicked while holding it "
        "(idiomatic poison propagation, low priority)"
    ),
    "thread_join": (
        "unwrapping a thread join re-raises that thread's panic (idiomatic, low priority)"
    ),
    "invariant_expect": (
        "expect panics with its stated message on failure, the idiomatic form when "
        "failure is a bug or makes start-up impossible (low priority)"
    ),
    "unreachable": (
        "unreachable! asserts this branch cannot run and panics only if it does "
        "(idiomatic, low priority)"
    ),
}


class ErrorHandlingDetector:
    name = "error_handling"
    category = "error_handling"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.error_handling_hits:
            idiom_reason = _IDIOM_REASONS.get(hit.idiom) if hit.idiom else None
            details: dict[str, str | float] = {"kind": hit.kind}
            if idiom_reason is not None:
                # ``deduction`` in details is what a history refresh replays.
                details.update(idiom=hit.idiom, deduction=0.0)
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=None,
                    line_start=hit.line,
                    line_end=hit.line,
                    details=details,
                    reason=idiom_reason or _REASONS.get(hit.kind, hit.kind),
                    deduction=0.0 if idiom_reason is not None else None,
                )
            )
        return out


BIOMARKER = ErrorHandlingDetector()

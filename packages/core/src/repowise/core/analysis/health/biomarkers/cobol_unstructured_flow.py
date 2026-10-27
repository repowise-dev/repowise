"""``cobol_unstructured_flow``: a COBOL paragraph or section whose control leaves
structured flow through ``ALTER``, ``GO TO ... DEPENDING ON`` or a ``GO TO``
that is not its range's exit (``health/complexity/cobol.py`` decides each jump).

One finding per procedure, at its first jump. Advisory: no defect corpus labels
it, so it is listed and never deducts.
"""

from __future__ import annotations

from ..complexity.cobol import UNSTRUCTURED_FLOW
from ..complexity.models import PerfHit
from ..models import Severity
from .base import BiomarkerResult, FileContext


def _reason(name: str, first: str, count: int) -> str:
    upper = first.upper()
    if upper.startswith("ALTER"):
        what = f"{first} rewrites a GO TO target at runtime, so where {name} sends control"
        what += " depends on which paragraph ran before it"
    elif "DEPENDING" in upper:
        what = f"{first} picks {name}'s next paragraph by value; a reader must trace"
        what += " each target by hand"
    elif upper == "GO TO":
        what = f"GO TO with no target in {name} goes wherever an ALTER last pointed it"
    else:
        what = f"{first} leaves {name} outside its PERFORM range; the paragraph's exit"
        what += " depends on control flow a reader must trace by hand"
    more = count - 1
    return what if not more else f"{what} (and {more} more jump{'s' if more > 1 else ''})"


class CobolUnstructuredFlowDetector:
    name = UNSTRUCTURED_FLOW
    category = "cobol"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        by_procedure: dict[tuple[str | None, int], list[PerfHit]] = {}
        for hit in ctx.perf_hits:
            if hit.kind == self.name:
                by_procedure.setdefault((hit.function, hit.func_start), []).append(hit)
        results: list[BiomarkerResult] = []
        for (function, _), hits in by_procedure.items():
            hits.sort(key=lambda h: h.line)
            first = hits[0]
            results.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=function,
                    line_start=first.line,
                    line_end=hits[-1].line,
                    details={
                        "count": len(hits),
                        "first_line": first.line,
                        # The first few statements; ``count`` carries the rest.
                        "jumps": [h.detail for h in hits[:5]],
                    },
                    reason=_reason(function or "the program", first.detail, len(hits)),
                )
            )
        return results

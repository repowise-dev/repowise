"""Whether a code-health figure counts change history or only code shape.

Roughly half of a file's deduction comes from git-derived markers — churn,
co-change, ownership, prior fixes — which rise as a file is worked on. That is
correct for predicting defects and useless for answering "is my code getting
better", so the page lets a reader drop that half and score the code alone.

The projection needs no rescore: both halves are stored per file, and the
scorer's own arithmetic is ``clamp(SCORE_MAX - structure - history)``, so
leaving one out is a subtraction rather than a second pass over the findings.
"""

from __future__ import annotations

from typing import Any, Literal

from .scoring import SCORE_FLOOR, SCORE_MAX

HealthCounts = Literal["everything", "code_shape"]
DEFAULT_COUNTS: HealthCounts = "everything"
COUNTS: tuple[HealthCounts, ...] = ("everything", "code_shape")


def parse_counts(value: str | None) -> HealthCounts:
    """Read a value, falling back to the default on anything unknown.

    The REST layer declares a pattern and refuses a bad value before this runs,
    so the fallback is for internal callers passing a stored or computed string.
    """
    return value if value in COUNTS else DEFAULT_COUNTS  # type: ignore[return-value]


def code_shape_score(structure_deduction: float | None) -> float | None:
    """A file's score with the history half removed, clamped like any score.

    ``None`` when the split was never recorded, which is every row written
    before it existed. Coercing a missing half to zero would print a confident
    10.0 for a file nobody has measured.
    """
    if structure_deduction is None:
        return None
    return max(SCORE_FLOOR, min(SCORE_MAX, SCORE_MAX - structure_deduction))


class CodeShapeMetric:
    """One metric row read with the history half of its score removed.

    A wrapper rather than a mutation. The rows handed to a route are live ORM
    objects, so assigning a projected score to one would mark it dirty and
    write the projection back to the store on the next flush.
    """

    # ``score`` is the surfaced number every aggregate weights, and
    # ``defect_score`` mirrors it for the REST serializer, which emits both.
    # ``history_deduction`` reads 0.0 because that is what this reading counts,
    # which keeps the figures derived from the two halves — the REST row's
    # ``unclamped_score``, the summary's history average — in step with the
    # score beside them. Everything else falls through to the row.
    __slots__ = ("_row", "defect_score", "history_deduction", "score")

    def __init__(self, row: Any, score: float) -> None:
        object.__setattr__(self, "_row", row)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "defect_score", score)
        object.__setattr__(self, "history_deduction", 0.0)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._row, name)


def project(counts: str, metrics: list[Any]) -> tuple[list[Any], int]:
    """Re-read *metrics* under *counts*, dropping rows it cannot answer for.

    Returns the projected rows and how many were dropped for want of a
    recorded split, so a caller can say "not measured" rather than imply the
    repository shrank.
    """
    if parse_counts(counts) != "code_shape":
        return metrics, 0
    out: list[Any] = []
    unmeasured = 0
    for m in metrics:
        score = code_shape_score(getattr(m, "structure_deduction", None))
        if score is None:
            unmeasured += 1
            continue
        out.append(CodeShapeMetric(m, score))
    return out, unmeasured

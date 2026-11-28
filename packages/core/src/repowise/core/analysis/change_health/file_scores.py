"""Per-file base and head scores for a change, read off both in-memory passes.

The passes see only the changed files' bytes, with no git history or coverage,
so each score is ``10 - structure`` over those bytes: comparable side to side.
It is not the index's ``counts="code_shape"`` score, whose structure half also
holds coverage markers and clones with files outside the change.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..health import HealthFileMetricData
from ..health.counts import code_shape_score

#: Name kept for compatibility: code shape of the changed bytes, no coverage.
SCORE_BASIS = "structure_only"


@dataclass(frozen=True, slots=True)
class FileScoreDelta:
    """One changed file's score and structure deduction on each side."""

    path: str
    base_score: float | None
    head_score: float | None
    base_structure: float | None
    head_structure: float | None
    nloc_base: int | None
    nloc_head: int | None
    #: A deleted file's code moved to these files.
    moved_to: tuple[str, ...] = ()

    @property
    def structure_change(self) -> float:
        """Signed structure deduction change, a missing side read as no deduction."""
        return (self.head_structure or 0.0) - (self.base_structure or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "base_score": _round(self.base_score),
            "head_score": _round(self.head_score),
            "base_structure": _round(self.base_structure),
            "head_structure": _round(self.head_structure),
            "nloc_base": self.nloc_base,
            "nloc_head": self.nloc_head,
            **({"moved_to": list(self.moved_to)} if self.moved_to else {}),
        }


@dataclass(frozen=True, slots=True)
class ScoreChange:
    """The changed files' average on each side, weighted by base nloc."""

    base: float
    head: float
    structure_delta: float
    files_compared: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "base": round(self.base, 2),
            "head": round(self.head, 2),
            "delta": round(round(self.head, 2) - round(self.base, 2), 2),
            "structure_delta": round(self.structure_delta, 2),
            "weighting": "base_nloc",
            "files_compared": self.files_compared,
        }


def file_score_deltas(
    base: Mapping[str, HealthFileMetricData],
    head: Mapping[str, HealthFileMetricData],
    paths: Iterable[str],
    rename: Mapping[str, str],
    *,
    departed: Mapping[str, list[str]] | None = None,
    full: bool = False,
) -> list[FileScoreDelta]:
    """A row per head path, largest structure move first. *rename* is ``{base: head}``.

    *departed* maps each deleted file to where its code moved; it gets a base-only row.
    *full* keeps each side's whole score, for a pass that read history and coverage.
    """
    base_of = {new: old for old, new in rename.items()}
    score = _full_score if full else _shape_score
    rows = [
        _row(path, base.get(base_of.get(path, path)), head.get(path), score) for path in paths
    ]
    rows += [
        replace(_row(path, base.get(path), None, score), moved_to=tuple(moved))
        for path, moved in (departed or {}).items()
    ]
    return sorted(rows, key=lambda r: (-abs(r.structure_change), r.path))


def score_change(rows: Iterable[FileScoreDelta]) -> ScoreChange | None:
    """Both sides' average over files scored on both, or ``None`` when there are none.

    Weighted by base nloc on both sides, so a file that grows while it improves
    does not cancel its own gain.
    """
    paired = [
        r
        for r in rows
        if r.base_score is not None and r.head_score is not None and (r.nloc_base or 0) > 0
    ]
    if not paired:
        return None
    weight = sum(r.nloc_base or 0 for r in paired)

    def mean(values: Iterable[float]) -> float:
        return sum(v * (r.nloc_base or 0) for v, r in zip(values, paired, strict=True)) / weight

    return ScoreChange(
        base=mean(r.base_score or 0.0 for r in paired),
        head=mean(r.head_score or 0.0 for r in paired),
        structure_delta=mean(
            (r.head_structure or 0.0) - (r.base_structure or 0.0) for r in paired
        ),
        files_compared=len(paired),
    )


def _row(
    path: str,
    base: HealthFileMetricData | None,
    head: HealthFileMetricData | None,
    score: Callable[[HealthFileMetricData | None], float | None],
) -> FileScoreDelta:
    return FileScoreDelta(
        path=path,
        base_score=score(base),
        head_score=score(head),
        base_structure=base.structure_deduction if base else None,
        head_structure=head.structure_deduction if head else None,
        nloc_base=base.nloc if base else None,
        nloc_head=head.nloc if head else None,
    )


def _shape_score(metric: HealthFileMetricData | None) -> float | None:
    if metric is None or metric.score is None:
        return None
    return code_shape_score(metric.structure_deduction)


def _full_score(metric: HealthFileMetricData | None) -> float | None:
    return metric.score if metric is not None else None


def _round(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None

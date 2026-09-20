"""Change-coupling response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CouplingNodeResponse(BaseModel):
    file_path: str
    #: ``None`` when the file has no health metric row.
    module: str | None = None
    score: float | None = None
    nloc: int = 0


class CouplingEdgeResponse(BaseModel):
    #: Lexicographically-smaller path, so a pair has one stable orientation.
    source: str
    target: str
    #: Decay-weighted co-change score, not a percentage.
    strength: float
    last_co_change: str | None = None
    #: Commits that touched both files, undecayed.
    support: int = 0
    #: Share of ``source``'s commits that also touched ``target``, and the
    #: reverse. They differ whenever one file changes more often than the other.
    confidence_ab: float | None = None
    confidence_ba: float | None = None
    #: Whether the dependency graph explains the pair: ``corroborated``,
    #: ``unexplained``, or ``not_applicable`` when a side is not in the graph.
    structural: Literal["corroborated", "unexplained", "not_applicable"] | None = None
    #: The graph edge behind a ``corroborated`` verdict (``imports``,
    #: ``type_use``, ``framework``, ...). ``None`` for every other verdict.
    dependency_kind: str | None = None


class CouplingGraphResponse(BaseModel):
    nodes: list[CouplingNodeResponse] = Field(default_factory=list)
    edges: list[CouplingEdgeResponse] = Field(default_factory=list)
    #: Pre-cap edge count, for an honest "showing N of M" line.
    total_edges: int = 0
    #: Distinct files spanned by those pre-cap pairs, over every file with
    #: commit history, so the count has a scale.
    coupled_files: int = 0
    total_files: int = 0

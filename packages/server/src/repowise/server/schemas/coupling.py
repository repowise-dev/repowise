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
    #: Whether the two files sit in different areas at the response's depth.
    cross_area: bool | None = None


class CouplingGraphResponse(BaseModel):
    nodes: list[CouplingNodeResponse] = Field(default_factory=list)
    edges: list[CouplingEdgeResponse] = Field(default_factory=list)
    #: Pre-cap edge count, for an honest "showing N of M" line.
    total_edges: int = 0
    #: Distinct files spanned by those pre-cap pairs, over every file with
    #: commit history, so the count has a scale.
    coupled_files: int = 0
    total_files: int = 0
    #: Area depth ``cross_area`` was placed at: the asked one, else the default.
    depth: int | None = None


class CouplingFigures(BaseModel):
    """Sums over the file pairs an area or an area pair holds."""

    pairs: int
    #: Pairs the dependency graph could explain and does not.
    unexplained_pairs: int
    #: ``unexplained_pairs`` over ``pairs``, 0-1.
    unexplained_share: float
    #: Summed decay-weighted strength, not a percentage.
    strength: float
    #: Shared commits summed per file pair: a commit counts once per pair it touched.
    co_changes: int
    last_co_change: str | None = None
    #: Area pairs only: a dependency joins the two areas in either direction.
    linked: bool | None = None
    #: Area pairs only: unexplained file pairs and no dependency between the
    #: areas at all. The finding; layered areas that import each other are not.
    hidden: bool | None = None


class CouplingAreaSummary(CouplingFigures):
    """One area's coupling: every kept pair with a file in it."""

    path: str


class CouplingAreaPair(CouplingFigures):
    """Two areas whose files change together; ``source == target`` for same-area pairs."""

    source: str
    target: str
    #: Strongest unexplained file pairs first, then the strongest of the rest.
    top_pairs: list[CouplingEdgeResponse] = Field(default_factory=list)


class CouplingAreaTotals(BaseModel):
    #: File pairs the filters kept.
    pairs: int
    cross_area_pairs: int
    unexplained_pairs: int
    #: Area pairs before the cap.
    area_pairs: int
    #: Area pairs holding at least one unexplained file pair.
    unexplained_area_pairs: int
    #: Area pairs with unexplained file pairs and no dependency between the areas.
    hidden_area_pairs: int
    #: Pairs indexed before the dependency check ran: neither explained nor not.
    unlabelled_pairs: int


class CouplingAreasResponse(BaseModel):
    root: str
    depth: int
    totals: CouplingAreaTotals
    #: Every area a kept pair touches, unexplained-heaviest first.
    areas: list[CouplingAreaSummary] = Field(default_factory=list)
    #: Hidden first, then by unexplained pairs and strength; capped at the asked limit.
    area_pairs: list[CouplingAreaPair] = Field(default_factory=list)

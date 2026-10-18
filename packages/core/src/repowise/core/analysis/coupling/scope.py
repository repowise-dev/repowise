"""Which couplings a view covers, and which areas each one joins.

A coupling view is read at one *depth* under one *root* area: every file is
placed in the area :func:`~repowise.core.analysis.areas.area_at` names for it,
so ``packages/ui/src/a.tsx`` at depth 2 is in ``packages/ui``. A file outside
the root is placed one level down from the repository at the root's own depth,
so drilling into ``packages/ui`` still shows it changing with ``packages/web``.

The filters narrow edges before anything is counted, so the totals a view
prints describe the edges it holds. Pure: callers pass loaded edges and a test
classifier and get placed edges back.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from repowise.core.analysis.areas import ROOT_AREA, area_at, area_depth, in_area

from ...co_change import STRUCTURAL_UNEXPLAINED
from .graph import CouplingEdge

__all__ = [
    "MAX_DEPTH",
    "CouplingScope",
    "PlacedEdge",
    "area_placer",
    "place_edges",
]

#: Deepest level a view groups by. Below it areas are single files' folders.
#: The default depth is chosen by ``areas.choose_depth`` within this bound.
MAX_DEPTH = 6


@dataclass(frozen=True)
class CouplingScope:
    """The view: where it is rooted, how deep it groups, and what it keeps."""

    depth: int
    root: str = ROOT_AREA
    #: Shared commits a pair needs to count.
    min_support: int = 0
    #: Only pairs the dependency graph could explain and does not.
    unexplained: bool = False
    #: Only pairs whose two files sit in different areas.
    cross_area: bool = False
    #: Leave out pairs touching test material. A test is expected to change
    #: with its code, the rule the hidden-coupling finding applies.
    exclude_tests: bool = False
    #: Only pairs that last changed together within this many days of the
    #: newest co-change in the index (not of today, so an old index still reads).
    recent_days: int | None = None
    #: Only pairs with a file placed in this area at ``depth``, and with
    #: ``partner`` its other file placed there: one area-pair row's file pairs.
    area: str | None = None
    partner: str | None = None


@dataclass(frozen=True, slots=True)
class PlacedEdge:
    """An edge with the two areas its files sit in, in the edge's own order."""

    edge: CouplingEdge
    source_area: str
    target_area: str


def area_placer(scope: CouplingScope) -> Callable[[str], str]:
    """The area *scope* places a file in, memoised per path. One placer for
    co-change pairs and dependency edges, so the two always name the same area."""
    floor = area_depth(scope.root)
    areas: dict[str, str] = {}

    def area_of(path: str) -> str:
        found = areas.get(path)
        if found is None:
            depth = floor + scope.depth if in_area(path, scope.root) else floor
            found = areas[path] = area_at(path, depth)
        return found

    return area_of


def _cutoff(edges: Sequence[CouplingEdge], days: int | None) -> str | None:
    """The oldest ``last_co_change`` a recency filter keeps, as an ISO date."""
    if days is None:
        return None
    newest = max((e.last_co_change[:10] for e in edges if e.last_co_change), default=None)
    if newest is None:
        return None
    try:
        return (date.fromisoformat(newest) - timedelta(days=days)).isoformat()
    except ValueError:
        return None


def _between(placed: PlacedEdge, area: str, partner: str | None) -> bool:
    """Whether the pair has a file placed in *area* and, given *partner*, its
    other file placed there. Placed areas, not path prefixes, so the root-files
    area ``""`` names only the files at the root, as its area-pair row does."""
    ends = (placed.source_area, placed.target_area)
    for here, there in (ends, ends[::-1]):
        if here == area and (partner is None or there == partner):
            return True
    return False


def _kept(
    e: CouplingEdge, scope: CouplingScope, cutoff: str | None, is_test: Callable[[str], bool]
) -> bool:
    """Whether *e* passes every filter that needs no area placement."""
    if not (in_area(e.source, scope.root) or in_area(e.target, scope.root)):
        return False
    if e.support < scope.min_support:
        return False
    if scope.unexplained and e.structural != STRUCTURAL_UNEXPLAINED:
        return False
    if cutoff is not None and (e.last_co_change or "")[:10] < cutoff:
        return False
    return not (scope.exclude_tests and (is_test(e.source) or is_test(e.target)))


def place_edges(
    edges: Sequence[CouplingEdge],
    scope: CouplingScope,
    *,
    is_test: Callable[[str], bool],
) -> list[PlacedEdge]:
    """The edges *scope* keeps, each stamped ``cross_area`` and placed in its areas.

    Order is preserved, so strongest-first input stays strongest first. An
    edge needs a file under the root to be in the view.
    """
    cutoff = _cutoff(edges, scope.recent_days)
    area_of = area_placer(scope)
    out: list[PlacedEdge] = []
    for e in edges:
        if not _kept(e, scope, cutoff, is_test):
            continue
        a, b = area_of(e.source), area_of(e.target)
        if scope.cross_area and a == b:
            continue
        placed = PlacedEdge(replace(e, cross_area=a != b), a, b)
        if scope.area is None or _between(placed, scope.area, scope.partner):
            out.append(placed)
    return out

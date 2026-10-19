"""Coupling between areas: which directories change together, and how much of
it the dependency graph cannot explain.

Folds placed file pairs (:func:`~.scope.place_edges`) into area pairs. An
area pair is *hidden* only when no dependency edge joins the two areas in
either direction: across a layer boundary (an app and the package it imports)
most file pairs never import each other directly, so the file-level
``unexplained`` label alone would flag every layered pair. An
area pair's figures are sums over its file pairs, so ``co_changes`` counts a
commit once per file pair it touched, not once per commit: the distinct-commit
count is not stored. Pure: no DB access.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from repowise.core.analysis.areas import area_at, area_depth, in_area

from ...co_change import STRUCTURAL_UNEXPLAINED
from .graph import CouplingEdge
from .scope import PlacedEdge

__all__ = [
    "AREA_PAIR_LIMIT",
    "COUPLED_AREA_LIMIT",
    "TOP_PAIRS",
    "area_coupling",
    "coupled_areas",
    "linked_area_pairs",
]

#: Area pairs one answer carries, ranked; the total is always reported.
AREA_PAIR_LIMIT = 50
#: File pairs carried under each area pair.
TOP_PAIRS = 5
#: Areas an area card lists as changing with it.
COUPLED_AREA_LIMIT = 5


@dataclass
class _Tally:
    """Running sums for one area or one area pair."""

    pairs: int = 0
    unexplained: int = 0
    strength: float = 0.0
    co_changes: int = 0
    last: str | None = None
    edges: list[CouplingEdge] = field(default_factory=list)

    def add(self, edge: CouplingEdge, *, keep: bool = False) -> None:
        self.pairs += 1
        self.unexplained += edge.structural == STRUCTURAL_UNEXPLAINED
        self.strength += edge.strength
        self.co_changes += edge.support
        if edge.last_co_change and (self.last is None or edge.last_co_change > self.last):
            self.last = edge.last_co_change
        if keep:
            self.edges.append(edge)

    def figures(self) -> dict[str, Any]:
        return {
            "pairs": self.pairs,
            "unexplained_pairs": self.unexplained,
            "unexplained_share": round(self.unexplained / self.pairs, 3) if self.pairs else 0.0,
            "strength": round(self.strength, 3),
            "co_changes": self.co_changes,
            "last_co_change": self.last,
        }


AreaKey = tuple[str, str]


def _key(a: str, b: str) -> AreaKey:
    return (a, b) if a <= b else (b, a)


def linked_area_pairs(
    dependencies: Iterable[tuple[str, str]], place: Callable[[str], str]
) -> set[AreaKey]:
    """Area pairs a dependency joins, in either direction, from file-level
    ``(importer, imported)`` edges placed by *place* (the view's placer)."""
    return {_key(place(src), place(dst)) for src, dst in dependencies}


def _verdict(key: AreaKey, tally: _Tally, linked: Collection[AreaKey]) -> dict[str, bool]:
    """``hidden`` only when no dependency joins the two areas at all and some
    file pair could have carried one: file pairs across a layer boundary rarely
    import each other directly, so a file-level gap alone is no finding."""
    is_linked = key in linked or key[0] == key[1]
    return {"linked": is_linked, "hidden": not is_linked and tally.unexplained > 0}


def _rank(item: tuple[AreaKey, _Tally], linked: Collection[AreaKey]) -> tuple[bool, int, float]:
    """Hidden area pairs first, the finding; then unexplained pairs, then strength."""
    key, tally = item
    return (_verdict(key, tally, linked)["hidden"], tally.unexplained, tally.strength)


def _top_edges(edges: list[CouplingEdge], limit: int) -> list[CouplingEdge]:
    """The strongest unexplained pairs first, then the strongest of the rest."""
    unexplained = [e for e in edges if e.structural == STRUCTURAL_UNEXPLAINED]
    rest = [e for e in edges if e.structural != STRUCTURAL_UNEXPLAINED]
    return (unexplained + rest)[:limit]


def area_coupling(
    placed: Sequence[PlacedEdge],
    *,
    linked: Collection[AreaKey] = frozenset(),
    limit: int = AREA_PAIR_LIMIT,
    top_pairs: int = TOP_PAIRS,
) -> dict[str, Any]:
    """Area pairs and per-area totals over *placed* edges.

    *placed* is strongest first (as :func:`~.scope.place_edges` keeps it), so
    each pair's file list is too. *linked* is :func:`linked_area_pairs` under
    the same placement. A pair inside one area is an area pair with itself,
    present only when the scope kept same-area edges, and never hidden.
    """
    pairs: dict[AreaKey, _Tally] = {}
    areas: dict[str, _Tally] = {}
    cross = unexplained = unlabelled = 0
    for p in placed:
        key = _key(p.source_area, p.target_area)
        pairs.setdefault(key, _Tally()).add(p.edge, keep=True)
        for area in set(key):
            areas.setdefault(area, _Tally()).add(p.edge)
        cross += key[0] != key[1]
        unexplained += p.edge.structural == STRUCTURAL_UNEXPLAINED
        unlabelled += p.edge.structural is None

    ranked = sorted(pairs.items(), key=lambda kv: _rank(kv, linked), reverse=True)
    return {
        "totals": {
            "pairs": len(placed),
            "cross_area_pairs": cross,
            "unexplained_pairs": unexplained,
            "area_pairs": len(pairs),
            "unexplained_area_pairs": sum(1 for t in pairs.values() if t.unexplained),
            "hidden_area_pairs": sum(1 for kv in pairs.items() if _verdict(*kv, linked)["hidden"]),
            # Pairs from an index written before the dependency check: no
            # verdict either way, so a view must not call them clean.
            "unlabelled_pairs": unlabelled,
        },
        "areas": [
            {"path": area, **tally.figures()}
            for area, tally in sorted(
                areas.items(), key=lambda kv: (kv[1].unexplained, kv[1].strength), reverse=True
            )
        ],
        "area_pairs": [
            {
                "source": key[0],
                "target": key[1],
                **tally.figures(),
                **_verdict(key, tally, linked),
                "top_pairs": [asdict(e) for e in _top_edges(tally.edges, top_pairs)],
            }
            for key, tally in ranked[:limit]
        ],
    }


def coupled_areas(
    edges: Sequence[CouplingEdge],
    area: str,
    *,
    dependencies: Iterable[tuple[str, str]] = (),
    keep: Callable[[CouplingEdge], bool] = lambda e: True,
    limit: int = COUPLED_AREA_LIMIT,
) -> list[dict[str, Any]]:
    """The areas outside *area* its files change with, most strongly first.

    A partner file is named by its area at *area*'s own depth, so
    ``packages/ui`` lists ``packages/web`` and ``tests/unit``, not single
    folders; each carries ``linked``/``hidden`` from *dependencies* as an area
    pair does. The root has nothing outside it and lists nothing.
    """
    depth = area_depth(area)
    if depth == 0:
        return []

    def outside(src: str, dst: str) -> str | None:
        """The other file's area when exactly one of the two is in *area*."""
        inside_a, inside_b = in_area(src, area), in_area(dst, area)
        if inside_a == inside_b:
            return None
        return area_at(dst if inside_a else src, depth)

    tallies: dict[str, _Tally] = {}
    for e in edges:
        other = outside(e.source, e.target)
        if other is not None and keep(e):
            tallies.setdefault(other, _Tally()).add(e)
    linked = {_key(area, o) for o in (outside(s, d) for s, d in dependencies) if o is not None}
    ranked = sorted(tallies.items(), key=lambda kv: kv[1].strength, reverse=True)
    return [
        {"path": path, **tally.figures(), **_verdict(_key(area, path), tally, linked)}
        for path, tally in ranked[:limit]
    ]

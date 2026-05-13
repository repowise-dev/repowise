"""Composite importance scoring for code symbols.

Symbols are ranked by a blend of architectural and structural signals that
already live in the database — no new ingestion pass required. The formula
favours symbols that sit at the centre of the dependency graph, are
externally visible, are non-trivial in size, and are flagged as entry
points.

The score is monotonic and bounded in [0, 1] per term, so it's safe to
compute server-side as a SQL expression for ORDER BY without materializing
the full list.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Weights — kept in one place so future tuning has a single touch-point.
W_PAGERANK = 0.40
W_VISIBILITY = 0.25
W_COMPLEXITY = 0.20
W_KIND = 0.10
W_ENTRY_POINT = 0.05

# Complexity is log-normalized against this ceiling so that a 30-branch
# function reaches the max signal — anything beyond is squashed.
_COMPLEXITY_CAP = 30.0
_COMPLEXITY_LOG_CEILING = math.log(_COMPLEXITY_CAP + 1.0)

# Symbol-kind multiplier. Classes and interfaces tend to carry more
# architectural weight than free functions; variables and constants less.
_KIND_BOOST: dict[str, float] = {
    "class": 1.2,
    "interface": 1.15,
    "trait": 1.15,
    "struct": 1.1,
    "enum": 1.05,
    "module": 1.05,
    "function": 1.0,
    "method": 1.0,
    "type": 0.9,
    "variable": 0.7,
    "constant": 0.7,
}
_DEFAULT_KIND_BOOST = 1.0


@dataclass(frozen=True)
class ImportanceComponents:
    file_pagerank: float
    visibility_factor: float
    complexity_norm: float
    kind_boost: float
    is_entry_point: bool

    def score(self) -> float:
        return (
            W_PAGERANK * self.file_pagerank
            + W_VISIBILITY * self.visibility_factor
            + W_COMPLEXITY * self.complexity_norm
            + W_KIND * (self.kind_boost / 1.2)  # normalize to [0, 1]
            + W_ENTRY_POINT * (1.0 if self.is_entry_point else 0.0)
        )


def _visibility_factor(visibility: str | None) -> float:
    if not visibility:
        return 0.5
    v = visibility.lower()
    if v == "public":
        return 1.0
    if v == "protected":
        return 0.75
    return 0.4


def _complexity_norm(complexity: int | None) -> float:
    if not complexity or complexity <= 0:
        return 0.0
    return min(1.0, math.log(complexity + 1.0) / _COMPLEXITY_LOG_CEILING)


def _kind_boost(kind: str | None) -> float:
    if not kind:
        return _DEFAULT_KIND_BOOST
    return _KIND_BOOST.get(kind.lower(), _DEFAULT_KIND_BOOST)


def compute_components(
    *,
    file_pagerank: float | None,
    visibility: str | None,
    complexity: int | None,
    kind: str | None,
    is_entry_point: bool | None,
) -> ImportanceComponents:
    """Pure-Python score components — used to enrich already-fetched rows."""

    return ImportanceComponents(
        file_pagerank=float(file_pagerank or 0.0),
        visibility_factor=_visibility_factor(visibility),
        complexity_norm=_complexity_norm(complexity),
        kind_boost=_kind_boost(kind),
        is_entry_point=bool(is_entry_point),
    )


@dataclass(frozen=True)
class RankedSymbol:
    """A WikiSymbol paired with the components/score used to rank it."""

    symbol: object  # WikiSymbol — kept loose so this module doesn't pull SQLAlchemy types
    components: ImportanceComponents
    file_pagerank: float
    is_entry_point: bool
    score: float


def rank_symbols(
    symbols: list[object],
    file_signals: dict[str, tuple[float, bool]],
) -> list[RankedSymbol]:
    """Annotate symbols with importance components and return them ordered by
    descending score (with name as the tiebreaker for stable pagination).

    ``file_signals`` maps ``file_path`` → ``(pagerank, is_entry_point)`` so
    callers can fetch the GraphNode rows once and reuse them.
    """

    ranked: list[RankedSymbol] = []
    for sym in symbols:
        path = getattr(sym, "file_path", None)
        pagerank, entry = file_signals.get(path or "", (0.0, False))
        components = compute_components(
            file_pagerank=pagerank,
            visibility=getattr(sym, "visibility", None),
            complexity=getattr(sym, "complexity_estimate", 0),
            kind=getattr(sym, "kind", None),
            is_entry_point=entry,
        )
        ranked.append(
            RankedSymbol(
                symbol=sym,
                components=components,
                file_pagerank=pagerank,
                is_entry_point=entry,
                score=components.score(),
            )
        )

    # Stable tiebreaker on name keeps pagination deterministic across pages.
    ranked.sort(key=lambda r: (-r.score, getattr(r.symbol, "name", "")))
    return ranked

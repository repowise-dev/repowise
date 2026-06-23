"""Pure ranking and candidacy rules for orientation entry points.

The orientation entry-point list answers one question: *where does execution
start?* That is not the same as *what is most central?* Centrality signals
(PageRank, betweenness) reward fan-in — a widely-imported resolver hub scores
high precisely because everything depends on it, which makes it a sink, the
opposite of a front door. Ranking by centrality therefore floats infrastructure
glue (a language resolver's ``index.py``) above the real ``main.py``.

This module ranks candidates by execution-start evidence instead — a
conventional entry filename and a shallow path — and uses centrality only to
break ties. It is deliberately free of any DB/graph/LLM dependency so the
ordering can be unit-tested directly.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# Generic module stems that *dispatch or re-export* rather than start a program.
# ``index`` (a JS/TS barrel or a per-language resolver shell) and ``mod`` (a Rust
# module root) gather siblings; they are glue, not a control-flow front door.
# Distinct from the registry's broader generic-entry set (main/app/server/cli/…),
# which are genuine execution starts.
GLUE_STEMS: frozenset[str] = frozenset({"index", "mod"})

# A glue-stem file is only plausibly a real entry when it sits at or very near a
# package root. Buried deeper, it is a dispatch/re-export leaf (a resolver's
# ``index.py`` nested under ``ingestion/resolvers/dotnet/``), never where a
# reader enters the system.
SHALLOW_ENTRY_DEPTH = 1


def entry_point_depth(path: str) -> int:
    """Directory depth — 0 for a root file, 1 for one level deep, etc."""
    return max(0, len(PurePosixPath(path).parts) - 1)


def is_glue_leaf(path: str) -> bool:
    """True for a generic-glue stem nested below the shallow band.

    These define real symbols (so :func:`_is_barrel` keeps them) yet are
    dispatch/re-export leaves, not execution entry points — excluded from the
    orientation entry-point list and never seeded as a tour entry point.
    """
    return (
        PurePosixPath(path).stem.lower() in GLUE_STEMS
        and entry_point_depth(path) > SHALLOW_ENTRY_DEPTH
    )


def _name_bucket(path: str, conventional_stems: frozenset[str]) -> int:
    """0 = conventional entry name, 1 = neutral, 2 = generic glue stem."""
    stem = PurePosixPath(path).stem.lower()
    if stem in GLUE_STEMS:
        return 2
    if stem in conventional_stems:
        return 0
    return 1


def entry_point_rank_key(
    path: str,
    *,
    pagerank: float = 0.0,
    betweenness: float = 0.0,
    conventional_stems: frozenset[str] = frozenset(),
) -> tuple[int, int, float, str]:
    """Sort key for an entry-point candidate (ascending tuple = better entry).

    Most significant component first:

      1. **name bucket** — a conventional entry name (``main``/``app``/``cli``/
         ``manage``/…) never loses to a generic glue stem (``index``/``mod``),
         and glue never outranks a real entry.
      2. **path depth** — shallower first; a front door sits near its package
         root, so a deeply-nested module cannot outrank a shallow real entry.
      3. **centrality** (``pagerank + betweenness``), negated — a tiebreak only,
         since centrality rewards fan-in (backwards for an entry point).
      4. **path** — deterministic final tiebreak.
    """
    return (
        _name_bucket(path, conventional_stems),
        entry_point_depth(path),
        -(pagerank + betweenness),
        path,
    )


def rank_entry_points(
    candidates: list[tuple[str, float, float]],
    conventional_stems: frozenset[str],
) -> list[str]:
    """Rank ``(path, pagerank, betweenness)`` candidates, best entry first."""
    return [
        path
        for path, _pr, _bt in sorted(
            candidates,
            key=lambda c: entry_point_rank_key(
                c[0],
                pagerank=c[1],
                betweenness=c[2],
                conventional_stems=conventional_stems,
            ),
        )
    ]

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
from typing import Any

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

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

# Filename stems that name a real execution start, minus the glue stems above
# so ``index``/``mod`` cannot be both at once. Registry-derived rather than
# written out, so a language that adds an entry stem is covered here too.
# Defined here, next to the ranking that reads it, because the KG curator and
# the wiki surfaces must answer "is this a conventional entry name" the same
# way. The registry is a pure data table, so this module keeps its promise of
# no DB / graph / LLM dependency.
CONVENTIONAL_ENTRY_STEMS: frozenset[str] = _LANG_REGISTRY.entry_filename_stems() - GLUE_STEMS


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


def orientation_entry_points(repo_structure: Any, *, limit: int | None = None) -> list[str]:
    """The repository's entry points, ordered the way every surface should show them.

    ``RepoStructure.entry_points`` arrives ``sorted()`` by path
    (``ingestion/traverser.py:470``) — deterministic, and meaningless as an
    orientation order. Lexicographic order puts ``.github/`` first, then
    ``apps/``, ``benchmarks/``, ``crates/``, ``docs/``; ``src/main.py`` sorts
    near the end. Six surfaces read that field. One ranked it and five took a
    raw prefix, so a truncated list showed whatever sorted first: PowerToys led
    its list with ``.github/scripts/telemetry-pr-check.js``, fastapi with a
    German docs page, pydantic with ``docs/index.md``.

    Ranked, not filtered, which is the same trade the overview already makes:
    ``packages/cli/src/index.ts`` is a genuine package front door in a
    monorepo, so dropping every glue stem would lose it, while ranking demotes
    glue below every real entry without losing one. The knowledge-graph
    curator filters as well, because it owns a candidacy question this does
    not: it is choosing what may appear at all, not ordering what already has.

    ``CONVENTIONAL_ENTRY_STEMS`` is passed, which the overview's own call did
    not do — ``sorted(..., key=entry_point_rank_key)`` calls the key
    positionally, so its ``conventional_stems`` defaulted to empty. Without the
    set the key degenerates to "glue last, then shallowest first", and depth
    alone is a bad primary signal: on the 41 indexed repos under
    ``test-repos/`` it puts a ``.github/scripts`` helper ahead of
    ``src/runner/main.cpp``, ``packages/cli/README.md`` first on dub, and a
    Cypress config first on jhipster. With the set those lead with
    ``src/runner/main.cpp``, ``apps/web/lib/axiom/server.ts`` and
    ``src/main/docker/app.yml``.

    It is a net win and not a clean sweep. Of the 24 repos whose leader moves,
    two get worse, and one of those is the stem set's own doing: osv-scalibr
    demotes ``binary/scalibr/scalibr.go``, the actual product binary, below
    ``linter/plugger/main.go``, because a binary named after its repo is not a
    conventional stem and ``main`` is. See the backlog.

    Ceiling, deliberate: no centrality is threaded through, so the
    ``pagerank`` / ``betweenness`` tiebreak :func:`entry_point_rank_key`
    accepts stays at zero. Every caller here holds a ``RepoStructure`` and not
    a graph, and it only separates paths that already agree on name and depth.

    Separately, and NOT addressed here: ordering cannot fix candidacy, and
    candidacy is the larger defect. ``repo_structure.entry_points`` carries
    ``.github/workflows/main.yml``, ``README.md`` and test files, so some
    repos lead with one whatever the order. The knowledge-graph curator drops
    those (``kg_curation._not_an_execution_start``) and the wiki surfaces do
    not; sharing that rule is the follow-up. Same for the heuristic's own
    false positives: ``app`` is a generic entry stem, so a React
    ``src/components/App.tsx`` is published as an execution entry point, as is
    any ``run.py`` / ``server.ts`` at any depth.
    """
    paths = list(getattr(repo_structure, "entry_points", None) or [])
    ranked = sorted(
        paths,
        key=lambda p: entry_point_rank_key(p, conventional_stems=CONVENTIONAL_ENTRY_STEMS),
    )
    return ranked if limit is None else ranked[:limit]


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

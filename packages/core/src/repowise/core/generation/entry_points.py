"""Pure ranking rules for orientation entry points.

Candidacy — *may* this file be an entry point at all — lives in
:mod:`repowise.core.entry_candidacy`, which ingestion also reads. This module
owns the second question: given the candidates, which comes first?

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

Three callable shapes, one rule: :func:`orientation_entry_points` for a
``RepoStructure``, :func:`rank_entry_point_paths` for bare paths, and
:func:`rank_entry_points` for callers holding centrality.

One ordering of entry points is deliberately not this one:
``tour.tour_landmark_paths`` orders by ``score_entry_points`` and truncates,
because it answers a *selection* question — which files are guaranteed a page —
and its score reads the ``is_entry_point`` flag itself, which this key never
sees. Sharing the key there would decide page selection on filename, depth and
centrality with the one direct piece of evidence dropped.

**Ranking cannot rescue bad candidacy, and the corpus still shows it.** On
``spring-petclinic`` the Spring stamper flags 24 entity and controller classes
and never flags ``PetClinicApplication.java``, so no ordering over that set can
name the front door: the best this key can do is pick a different wrong file.
That is a candidacy gap, tracked separately.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from repowise.core.entry_candidacy import (
    GLUE_STEMS,
    conventional_entry_stems,
    entry_point_depth,
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


def rank_entry_point_paths(paths: Iterable[str]) -> list[str]:
    """Rank bare paths, best entry first, with no centrality to break ties.

    The sibling for callers that hold a set or a query result rather than a
    ``RepoStructure``. Centrality is only a tiebreak between paths that already
    agree on name and depth, and the path component keeps the order total, so
    omitting it costs nothing but determinism-preserving arbitrariness.
    Callers that do hold centrality should use :func:`rank_entry_points`.
    """
    stems = conventional_entry_stems()
    return sorted(paths, key=lambda p: entry_point_rank_key(p, conventional_stems=stems))


def orientation_entry_points(repo_structure: Any, *, limit: int | None = None) -> list[str]:
    """The repository's entry points, ordered the way every surface should show them.

    ``RepoStructure.entry_points`` arrives ``sorted()`` by path
    (``ingestion/traverser.py:480``) — deterministic, and meaningless as an
    orientation order. Lexicographic order puts ``.github/`` first, then
    ``apps/``, ``benchmarks/``, ``crates/``, ``docs/``; ``src/main.py`` sorts
    near the end. Six surfaces read that field. One ranked it and five took a
    raw prefix, so a truncated list showed whatever sorted first: PowerToys led
    its list with ``.github/scripts/telemetry-pr-check.js``, fastapi with a
    German docs page, pydantic with ``docs/index.md``.

    Ranked, not filtered — this function still holds no candidacy rule of its
    own. What changed is where candidacy runs: ``not_an_execution_start`` is
    now applied to the ingestion flag this list projects, so the input arrives
    already free of config files and deep glue leaves rather than carrying them
    to be demoted. That does cost the case this docstring used to argue: a deep
    ``packages/cli/src/index.ts`` was a genuine package front door that ranking
    kept and filtering loses. It was bought back deliberately, because the same
    flag also exempts a file from dead-code detection and the knowledge-graph
    curator was already dropping those paths outright, so the two surfaces
    disagreed about the same file. Ranking demotes glue that survives
    candidacy — a shallow ``cli/index.ts`` — below every real entry.

    The conventional-stem set is passed, which the overview's own call did
    not do — ``sorted(..., key=entry_point_rank_key)`` calls the key
    positionally, so its ``conventional_stems`` defaulted to empty. Without the
    set the key degenerates to "glue last, then shallowest first", and depth
    alone is a bad primary signal: measured on the 41 indexed repos it put a
    ``.github/scripts`` helper ahead of ``src/runner/main.cpp``.

    That measurement predates candidacy running at ingestion, and its other
    examples cannot recur: it also named ``packages/cli/README.md`` leading dub
    and ``src/main/docker/app.yml`` leading jhipster, and markdown and yaml are
    non-code languages that no longer reach this list at all. Ranking still
    needs the stem set — a neutrally-named code file at depth 1 would otherwise
    outrank ``src/runner/main.cpp`` on depth alone — but the case for it is now
    narrower than the numbers it was argued from.

    It is a net win and not a clean sweep. Of the 24 repos whose leader moved
    in that measurement, two got worse, and one of those is the stem set's own
    doing: osv-scalibr demotes ``binary/scalibr/scalibr.go``, the actual
    product binary, below ``linter/plugger/main.go``, because a binary named
    after its repo is not a conventional stem and ``main`` is. See the backlog.

    Ceiling, deliberate: no centrality is threaded through, so the
    ``pagerank`` / ``betweenness`` tiebreak :func:`entry_point_rank_key`
    accepts stays at zero. Every caller here holds a ``RepoStructure`` and not
    a graph, and it only separates paths that already agree on name and depth.

    What candidacy still does not cover, since ordering cannot fix it here:
    ``repo_structure.entry_points`` no longer carries
    ``.github/workflows/main.yml`` or ``README.md`` (both non-code languages),
    but it does still carry test files — the curator's adjacent-layer and
    support-path exclusions stay at the curator, where the layer map is. Same
    for the heuristic's own false positives: ``app`` is a generic entry stem,
    so a React ``src/components/App.tsx`` is published as an execution entry
    point, as is any ``run.py`` / ``server.ts`` at any depth.
    """
    ranked = rank_entry_point_paths(getattr(repo_structure, "entry_points", None) or [])
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

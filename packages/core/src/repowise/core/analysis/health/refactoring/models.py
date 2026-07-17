"""Structured refactoring suggestions — the spine of the refactoring layer.

A ``RefactoringSuggestion`` is the deterministic, structured output of a
``RefactoringDetector`` (see ``registry.py``). It replaces the old static
suggestion *string* with a record carrying the concrete plan, the evidence
behind it, and the blast radius of applying it. Human-readable text is
rendered from this structure at the edges (CLI / MCP / web); the structure
is the source of truth, never a string.

The schema is shared across refactoring types: ``plan`` / ``evidence`` /
``blast_radius`` are open dicts whose shape is type-specific (documented per
detector), so later phases (Extract Helper, Move Method, Break Cycle) add a
type without touching this module. For Extract Class:

- ``plan`` = ``{"groups": [{"name": None, "methods": [...], "fields": [...]}]}``
  — one group per cohesive cluster the class should split into.
- ``evidence`` = ``{"lcom4": int, "method_count": int, "field_count": int,
  "wmc": int}`` — the cohesion/size signals that justify the split.
- ``blast_radius`` = ``{"dependents_count": int}`` — files importing the
  class's file that may need to follow the split.

For Extract Helper:

- ``plan`` = ``{"occurrences": [{"file": str, "line_start": int,
  "line_end": int}, ...], "suggested_site": {"module": str | None,
  "directory": str | None}, "duplicated_lines": int}`` — every site of the
  duplicated block and where the shared helper should live.
- ``evidence`` = ``{"occurrence_count": int, "duplicated_lines": int,
  "token_count": int, "co_change_count": int, "is_intra_file": bool}`` — the
  size + activity signals that justify extracting a helper.
- ``blast_radius`` = ``{"files": [...], "file_count": int,
  "co_change_count": int}`` — the other files that must change in lockstep.

For Move Method:

- ``plan`` = ``{"method": str, "from_class": str, "to_class": str,
  "to_file": str | None}`` — the feature-envy method and where it belongs.
- ``evidence`` = ``{"foreign_calls": int, "own_calls": int,
  "own_distance": float, "target_distance": float}`` — the Jaccard distances
  and call counts that prove the method is closer to ``to_class`` than its own.
- ``blast_radius`` = ``{"callers": int, "files": [...]}`` — who calls the
  method (they keep working, but the move touches both classes' files).

For Break Cycle:

- ``plan`` = ``{"cycle": [str, ...], "cut_edges": [{"from": str, "to": str},
  ...]}`` — the files in the import cycle and the minimal set of import edges
  to invert/abstract to break it (greedy MFAS).
- ``evidence`` = ``{"cycle_size": int, "edge_count": int,
  "cut_count": int}`` — the SCC size, total intra-cycle edges, and cut size.
- ``blast_radius`` = ``{"files": [...], "file_count": int}`` — every file in
  the cycle (breaking it is a multi-file change).

For Split File:

- ``plan`` = ``{"groups": [{"name": str | None, "symbols": [str, ...],
  "suggested_file": str}], "residual": {"symbols": [...]} | None,
  "shim_required": bool}`` — the cohesive groups the file should split into
  (each with a suggested filename), the shared-utility ``core`` left behind,
  and whether a back-compat re-export shim is needed (false for same-package
  Go, true for Python/TS).
- ``evidence`` = ``{"file_nloc": int, "symbol_count": int, "group_count": int,
  "modularity": float, "intra_edges": int, "cut_edges": int}`` — the size and
  decomposability signals that justify (and gate) the split. Two optional keys
  are added only when the richer cohesion signals fired:
  ``"cochange_edges": int`` (symbol pairs joined by a git co-change edge) and
  ``"import_edges": int`` (pairs joined by a shared imported-name surface).
- ``blast_radius`` = ``{"dependent_files": [str, ...], "dependent_count": int,
  "import_rewrites": int}`` — the external files referencing the split
  symbols and how many import edits the split implies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Confidence buckets a detector may assign. Ordered low -> high; the surface
# layers may filter on a ``min_confidence`` config (default: medium).
CONFIDENCE_LEVELS = ("low", "medium", "high")


@dataclass
class RefactoringContext:
    """Per-file inputs a ``RefactoringDetector`` reads.

    Assembled by the health engine from data it has already computed (no
    re-parse): the file's class models (carrying LCOM4 ``components``), the
    file's biomarker findings (so a detector can read the ``health_impact``
    its refactoring would recover), the file's NLOC (for the effort bucket),
    and the file-level dependents count (the blast-radius seed). Detectors
    degrade to "no suggestion" on any missing signal — never a wrong one.
    """

    file_path: str
    language: str
    nloc: int
    # ``ClassComplexity`` records for the file (typed ``Any`` to avoid a
    # circular import with the complexity walker package).
    classes: list[Any] = field(default_factory=list)
    # The file's ``HealthFindingData`` findings (typed ``Any`` for the same
    # reason); a detector keys off these for the recovered impact.
    findings: list[Any] = field(default_factory=list)
    # Files importing this file (graph in-degree) — the blast-radius seed.
    dependents_count: int = 0
    # The file's clone pairs (``duplication.ClonePair`` records, typed ``Any``
    # for the same circular-import reason) — every pair touches this file.
    # Consumed by the Extract Helper detector. Empty when duplication is
    # disabled or the file has no clones.
    clones: list[Any] = field(default_factory=list)
    # Repo-wide file -> community/module label map (a shared reference, not a
    # per-file copy). The Extract Helper detector reads it to place the
    # extracted helper at the community centroid of the clone's occurrences.
    # Empty on small repos that produced no community labels — the detector
    # then falls back to a shared-directory site.
    module_map: dict[str, str] = field(default_factory=dict)
    # The repo's symbol/file graph (a ``networkx.DiGraph``, typed ``Any`` to
    # avoid importing networkx into the model layer). A shared read-only
    # reference — the graph-native detectors (Move Method, Break Cycle) read
    # call / has_method / imports edges off it. ``None`` when the health pass
    # ran without a graph; those detectors then degrade to "no suggestion".
    graph: Any = None
    # This file's strongly-connected component (sorted member file paths) when
    # it sits in a real import cycle (``size >= 2``); ``None`` otherwise. The
    # engine precomputes the repo's SCC index once and threads the per-file
    # slice in, so Break Cycle never recomputes SCCs per file.
    file_scc: tuple[str, ...] | None = None
    # Symbol ids of the methods defined in this file, sliced from the repo-wide
    # index the engine precomputes once (``graph_signals.build_methods_by_file``),
    # so Move Method never scans the whole graph per file. ``None`` means the
    # index was not provided (direct detector use) — the detector then derives
    # the list from the graph itself, exactly as before.
    file_methods: tuple[str, ...] | None = None
    # Per-flagged-function dataflow analyses (``dataflow.FunctionAnalysis``
    # records, typed ``Any`` to avoid importing the dataflow layer into the
    # model). The engine builds these only for files carrying a method-level
    # smell (``large_method`` / ``brain_method`` / ``complex_method``), so the
    # Extract Method detector reads CFG + def/use + reaching definitions without
    # a re-parse of its own. Empty for files with no such finding -- the
    # detector then yields nothing.
    function_analyses: list[Any] = field(default_factory=list)
    # This file's per-line blame index (``git_indexer.function_blame.BlameIndex``,
    # typed ``Any`` to avoid importing the ingestion layer into the model). A
    # shared read-only reference the engine already materialised for the
    # function-level biomarkers; Split File projects each top-level symbol's line
    # range through it for a co-change "keep-together" edge. ``None`` (or an empty
    # index) is the documented "no signal" outcome — the detector degrades to its
    # call/import signals only.
    blame_index: Any = None


@dataclass
class RefactoringSuggestion:
    """One deterministic refactoring opportunity. Persisted as a
    ``RefactoringSuggestion`` ORM row; rendered to text only at the edges.
    """

    refactoring_type: str  # "extract_class", "extract_helper", "move_method", ...
    file_path: str
    target_symbol: str  # the class / method / site the refactoring acts on
    line_start: int | None
    line_end: int | None
    # The concrete plan — shape is ``refactoring_type``-specific (see module
    # docstring). Always structured data, never prose.
    plan: dict[str, Any]
    # The signals that justify the suggestion (LCOM4, WMC, clone ranges, ...).
    evidence: dict[str, Any]
    # Health score the refactoring would recover if applied (the deduction of
    # the source biomarker finding). >= 0.
    impact_delta: float
    # Effort estimate — "S" | "M" | "L" | "XL" (from the target's size).
    effort_bucket: str
    # What else must change: callers, co-change partners, importing files.
    blast_radius: dict[str, Any]
    # "low" | "medium" | "high" — drives the ``min_confidence`` surface gate.
    confidence: str
    # The biomarker finding this suggestion answers (e.g. "low_cohesion").
    source_biomarker: str = ""

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
type without touching this module. For Extract Class (Phase 1):

- ``plan`` = ``{"groups": [{"name": None, "methods": [...], "fields": [...]}]}``
  — one group per cohesive cluster the class should split into.
- ``evidence`` = ``{"lcom4": int, "method_count": int, "field_count": int,
  "wmc": int}`` — the cohesion/size signals that justify the split.
- ``blast_radius`` = ``{"dependents_count": int}`` — files importing the
  class's file that may need to follow the split.

For Extract Helper (Phase 2):

- ``plan`` = ``{"occurrences": [{"file": str, "line_start": int,
  "line_end": int}, ...], "suggested_site": {"module": str | None,
  "directory": str | None}, "duplicated_lines": int}`` — every site of the
  duplicated block and where the shared helper should live.
- ``evidence`` = ``{"occurrence_count": int, "duplicated_lines": int,
  "token_count": int, "co_change_count": int, "is_intra_file": bool}`` — the
  size + activity signals that justify extracting a helper.
- ``blast_radius`` = ``{"files": [...], "file_count": int,
  "co_change_count": int}`` — the other files that must change in lockstep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Confidence buckets a detector may assign. Ordered low -> high; the surface
# layers may filter on a ``min_confidence`` config (Phase 1 default: medium).
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


@dataclass
class RefactoringSuggestion:
    """One deterministic refactoring opportunity. Persisted as a
    ``RefactoringSuggestion`` ORM row; rendered to text only at the edges.
    """

    refactoring_type: str  # "extract_class" (Phase 1); more in later phases
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

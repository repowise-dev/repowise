"""Extract Class detector — the flagship refactoring (Phase 1).

When a class's LCOM4 cohesion components splinter into two or more groups
(methods that share no fields or calls), each component is a candidate
extracted class. The split is not heuristic: it *is* the LCOM4 connected
components the walker already computed (``ClassComplexity.components``), so
the suggestion is deterministic and matches the ``low_cohesion`` /
``god_class`` biomarkers exactly.

The detector confirms the god-class shape with two Lanza-Marinescu signals
carried as evidence (WMC = Σ method McCabe; method/field counts), reads the
recovered health impact off the matching biomarker finding, and emits one
``RefactoringSuggestion`` per splittable class with the concrete group
breakdown.

Precision-first — Extract Class is about partitioning *shared state* into
cohesive classes, so the gate demands genuine state to partition:

- the class is one the cohesion biomarker already flags (``lcom4 >= 2``,
  ``method_count >= _MIN_METHODS``), so the list never exceeds what health
  surfaces;
- at least two *field-bearing* groups — the state splits into ≥2 independent
  stateful clusters, not "one core + loose helpers";
- a minimum field density (fields per method) — this rejects stateless
  strategy / dialect classes (many independent predicate methods, almost no
  shared state) that LCOM4 over-fires on but that should never be "split".

The displayed plan keeps only the substantive groups (field-bearing or
multi-method); lone fieldless helper methods don't constitute their own
extracted class and are dropped from the split so the plan reads honestly.
"""

from __future__ import annotations

from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, effort_bucket, register

# The cohesion biomarkers this detector answers. A class is only suggested
# for splitting if one of these flagged it, so the suggestion list never
# exceeds (and stays consistent with) what health already surfaces.
_SOURCE_BIOMARKERS = ("low_cohesion", "god_class")

# Mirror ``LowCohesionDetector._MIN_METHODS`` so a class we suggest splitting
# is exactly one the cohesion biomarker would also flag — never noise below
# its threshold.
_MIN_METHODS = 5

# Minimum shared-state density (instance fields per method) for a class to be
# a real Extract Class target. Stateless strategy/dialect classes — lots of
# independent predicate methods, near-zero shared fields — sit well below this
# (dogfooded at <=0.29 on our own ``*PerfDialect`` classes) and are rejected;
# genuinely stateful god classes sit at ~0.5 to 1.0.
_MIN_FIELD_DENSITY = 0.4


def _is_substantive(group: object) -> bool:
    """A group worth extracting on its own: ≥2 methods, or ≥1 method that
    touches ≥1 field. Filters out lone, fieldless helper methods so a class
    isn't sold as an N-way split when it is really "one class + loners"."""
    methods = getattr(group, "methods", [])
    fields = getattr(group, "fields", [])
    return len(methods) >= 2 or (len(methods) >= 1 and len(fields) >= 1)


@register
class ExtractClassDetector(RefactoringDetector):
    name = "extract_class"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        out: list[RefactoringSuggestion] = []
        impact_by_class = self._impact_by_class(ctx)

        for cls in ctx.classes:
            components = getattr(cls, "components", None) or []
            # Need a real multi-component split on a class big enough to matter
            # and stateful enough that splitting its fields makes sense.
            if cls.lcom4 < 2 or cls.method_count < _MIN_METHODS:
                continue
            field_density = cls.field_count / max(cls.method_count, 1)
            if field_density < _MIN_FIELD_DENSITY:
                continue
            # The split must partition real state into ≥2 stateful clusters.
            field_bearing = [g for g in components if getattr(g, "fields", None)]
            if len(field_bearing) < 2:
                continue
            # Present only the substantive groups; lone fieldless helpers don't
            # form their own class. Re-check we still have a ≥2-way split.
            substantive = [g for g in components if _is_substantive(g)]
            if len(substantive) < 2:
                continue

            wmc = sum(getattr(m, "ccn", 0) for m in getattr(cls, "methods", []))
            impact_delta, source = impact_by_class.get(cls.name, (0.0, ""))

            groups = [
                {"name": None, "methods": list(g.methods), "fields": list(g.fields)}
                for g in substantive
            ]
            out.append(
                RefactoringSuggestion(
                    refactoring_type=self.name,
                    file_path=ctx.file_path,
                    target_symbol=cls.name,
                    line_start=cls.start_line,
                    line_end=cls.end_line,
                    plan={"groups": groups},
                    evidence={
                        "lcom4": cls.lcom4,
                        "method_count": cls.method_count,
                        "field_count": cls.field_count,
                        "wmc": wmc,
                    },
                    impact_delta=round(float(impact_delta), 3),
                    effort_bucket=effort_bucket(cls.total_nloc),
                    blast_radius={"dependents_count": ctx.dependents_count},
                    confidence=self._confidence(cls),
                    source_biomarker=source or _SOURCE_BIOMARKERS[0],
                )
            )

        # Stable order: biggest recovery first, then by class name so ties
        # (and the no-finding case) are still deterministic.
        out.sort(key=lambda s: (-s.impact_delta, s.target_symbol))
        return out

    @staticmethod
    def _impact_by_class(ctx: RefactoringContext) -> dict[str, tuple[float, str]]:
        """Map class name -> (recovered impact, source biomarker) from the
        file's cohesion findings. The biomarkers report ``function_name`` =
        the class name; keep the largest impact when both fire on one class.
        """
        by_class: dict[str, tuple[float, str]] = {}
        for f in ctx.findings:
            if getattr(f, "biomarker_type", "") not in _SOURCE_BIOMARKERS:
                continue
            name = getattr(f, "function_name", None)
            if not name:
                # Fall back to the details payload both biomarkers carry.
                name = (getattr(f, "details", {}) or {}).get("class_name")
            if not name:
                continue
            impact = float(getattr(f, "health_impact", 0.0) or 0.0)
            prev = by_class.get(name)
            if prev is None or impact > prev[0]:
                by_class[name] = (impact, getattr(f, "biomarker_type", ""))
        return by_class

    @staticmethod
    def _confidence(cls: object) -> str:
        """High when the god-class shape is unambiguous (many groups / many
        methods); medium for a clean two-way split."""
        lcom4 = getattr(cls, "lcom4", 0)
        method_count = getattr(cls, "method_count", 0)
        if lcom4 >= 3 or method_count >= 15:
            return "high"
        return "medium"

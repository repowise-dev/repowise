"""Derive a repo-specific concept outline: deterministic grouping, LLM naming.

The split is not a style preference, it is what the probes measured. Feeding a
planner the whole directory inventory and asking it to both allocate files and
name the result gave 91.7% coverage on one run and 87.5% on the next from an
identical payload, and pushing harder on allocation dropped coverage to 88.2%
while raising fabricated paths ninefold. Membership decisions are the part an
LLM is unreliable at and a bounded-partition function is exact at; naming is
the part it is genuinely good at.

So :mod:`grouping` decides which files each page covers, with no model in the
loop, and :mod:`naming` decides what those pages are called. Coverage becomes
100% by construction rather than by luck, and the identity of a page stops
depending on a sampling temperature.
"""

from .grouping import ConceptGroup, GroupingParams, group_files
from .models import ConceptOutline, ConceptPage, ConceptSection, OutlineReport
from .validation import validate_outline

__all__ = [
    "ConceptGroup",
    "ConceptOutline",
    "ConceptPage",
    "ConceptSection",
    "GroupingParams",
    "OutlineReport",
    "group_files",
    "validate_outline",
]

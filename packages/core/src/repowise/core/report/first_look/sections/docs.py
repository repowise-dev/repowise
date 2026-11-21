"""Docs: broken references the ``broken_doc_refs`` rule trusts.

The rule's filter (``_trusted_drift``: confidence at least 0.8, a path this
repository once had, an in-repo anchor) decides what is listed, one item per
document. A document is the subject here, not code, so its origin is kept on
the item but does not filter it.
"""

from __future__ import annotations

from repowise.core.analysis.actions.rules.hygiene import broken_doc_refs

from ..context import FirstLookContext, UnavailableError
from ..model import (
    Collected,
    FirstLookSection,
    Item,
    Link,
    Location,
    Member,
    PromptRef,
    first_look_sections,
    item_id,
    locations,
)
from ._common import TIER_SEVERITY

SECTION = "docs"
#: Targets one item names in its facts; the rest are counted.
_TARGETS_SHOWN = 10


async def collect(ctx: FirstLookContext) -> Collected:
    outcome = broken_doc_refs(ctx.facts, ctx.rules)
    if outcome.status == "unavailable":
        raise UnavailableError(outcome.reason)
    items = []
    for action in outcome.actions:
        doc = action.target_path
        targets = sorted({d.ref for d in action.details if d.ref})
        locs, total = locations([Location(doc, d.line) for d in action.details])
        items.append(
            Item(
                id=item_id(SECTION, action.rule, doc),
                section=SECTION,
                rule=action.rule,
                title_facts={
                    "references": len(targets),
                    "kinds": sorted({d.marker for d in action.details if d.marker}),
                    "targets": targets[:_TARGETS_SHOWN],
                },
                locations=locs,
                locations_total=total,
                confidence=action.confidence,
                basis="inferred",
                severity=TIER_SEVERITY[action.tier],
                effort=action.effort,
                link=Link("docs_drift"),
                prompt=PromptRef("action", action.action_id),
                fingerprint=action.fingerprint,
                origin=ctx.origin(doc),
                priority=action.effective_priority,
                members=(Member("action", action.action_id, action.fingerprint),),
            )
        )
    return Collected(tuple(items))


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

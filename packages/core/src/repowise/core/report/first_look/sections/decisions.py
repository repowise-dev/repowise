"""Decisions: accepted decisions whose files changed since, through ``stale_decision``.

Mined proposals nobody reviewed are not listed. A repository with no accepted
decisions has no section; its "Turn on more" step comes from the setup rules.
"""

from __future__ import annotations

from repowise.core.analysis.actions.rules.hygiene import stale_decision

from ..context import FirstLookContext, UnavailableError
from ..model import (
    Collected,
    FirstLookSection,
    Item,
    Link,
    Member,
    PromptRef,
    first_look_sections,
    item_id,
)
from ._common import TIER_SEVERITY

SECTION = "decisions"


async def collect(ctx: FirstLookContext) -> Collected:
    outcome = stale_decision(ctx.facts, ctx.rules)
    if outcome.status == "unavailable":
        raise UnavailableError(outcome.reason)
    titles = {d.id: d.title for d in ctx.facts.stale_decisions}
    items = tuple(
        Item(
            id=item_id(SECTION, action.rule, action.target_path),
            section=SECTION,
            rule=action.rule,
            title_facts={"title": titles.get(action.target_path, "")},
            locations=(),
            confidence=action.confidence,
            basis="declared",
            severity=TIER_SEVERITY[action.tier],
            effort=action.effort,
            link=Link("decision", {"id": action.target_path}),
            prompt=PromptRef("action", action.action_id),
            fingerprint=action.fingerprint,
            priority=action.effective_priority,
            members=(Member("action", action.action_id, action.fingerprint),),
        )
        for action in outcome.actions
    )
    return Collected(items, {"accepted": ctx.facts.accepted_decisions})


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

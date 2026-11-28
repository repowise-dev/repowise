"""Security: credentials in the working tree, through the ``live_secret`` rule.

The rows are the ones ``repowise next`` reads (high severity, working tree,
credential kinds, outside tests), kept by the same rule filter
(``_looks_like_code``). An item holds the path, line and kind; never the
snippet, masked or not. A leaked credential is live whatever the file's
origin, so this section keeps every origin the rule keeps.
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.analysis.actions.model import ActionDetail, fingerprint
from repowise.core.analysis.actions.rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS, live_secret
from repowise.core.analysis.security_gate import fingerprint_of

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

SECTION = "security"


async def _history_only(ctx: FirstLookContext, listed: set[tuple[str, str, str]]) -> int:
    """Credentials found only in git history: one count line, never items.

    A history row for a secret still in the working tree is already an item,
    so it is left out. Matched on path, kind and snippet, the line-independent
    identity the security gate's fingerprint keys on (the line moves between
    commits).
    """
    from repowise.core.persistence.models import SecurityFinding

    rows = await ctx.session.execute(
        select(SecurityFinding.file_path, SecurityFinding.kind, SecurityFinding.snippet).where(
            SecurityFinding.repository_id == ctx.repository_id,
            SecurityFinding.commit_sha != "",
            SecurityFinding.severity == "high",
            SecurityFinding.kind.in_((*SECRET_KINDS, PUBLIC_ENV_KIND)),
        )
    )
    return sum((path, kind, snippet or "") not in listed for path, kind, snippet in rows)


async def collect(ctx: FirstLookContext) -> Collected:
    outcome = live_secret(ctx.facts, ctx.rules)
    if outcome.status == "unavailable":
        raise UnavailableError(outcome.reason)
    # The action holds no snippet; the masked one keys the gate's fingerprint.
    snippets = {(s.file_path, s.kind, s.line): s.snippet for s in ctx.facts.secrets}

    def snippet(d: ActionDetail) -> str:
        return snippets.get((d.path, d.marker or "", d.line), "")

    items = []
    for action in outcome.actions:
        path = action.target_path
        kinds = {d.marker for d in action.details if d.marker}
        kind = next((k for k in SECRET_KINDS if k in kinds), PUBLIC_ENV_KIND)
        locs, total = locations([Location(d.path, d.line) for d in action.details])
        items.append(
            Item(
                id=item_id(SECTION, kind, path),
                section=SECTION,
                rule=kind,
                title_facts={"kind": kind, "locations": total},
                locations=locs,
                locations_total=total,
                confidence=action.confidence,
                basis="inferred",
                severity=TIER_SEVERITY[action.tier],
                effort=action.effort,
                link=Link("security"),
                prompt=PromptRef("action", action.action_id),
                # The security gate's own fingerprint per finding, hashed again:
                # it moves when a value or its line set changes, and holds none.
                fingerprint=fingerprint(
                    *sorted(fingerprint_of(d.path, d.marker or "", snippet(d)) for d in action.details)
                ),
                origin=ctx.origin(path),
                priority=action.effective_priority,
                members=(Member("action", action.action_id, action.fingerprint),),
            )
        )
    listed = {
        (d.path, d.marker or "", snippet(d)) for action in outcome.actions for d in action.details
    }
    return Collected(tuple(items), {"history_only": await _history_only(ctx, listed)})


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

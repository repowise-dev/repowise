"""Ownership: how much of the code one person holds, counted, never named.

Read only through the assessment report's bus-factor section
(``report/sections/bus_factor.py``), per component.
"""

from __future__ import annotations

from repowise.core.analysis.actions.model import fingerprint

from ..context import FirstLookContext
from ..model import Collected, FirstLookSection, Item, Link, PromptRef, first_look_sections, item_id

SECTION = "ownership"

#: The one switch for this section. Bus factor counts one person once across
#: author names and emails, so the figure is safe to show.
OWNERSHIP_ENABLED = True


async def collect(ctx: FirstLookContext) -> Collected:
    if not OWNERSHIP_ENABLED or ctx.repo_root is None:
        return Collected()
    from repowise.core.report.model import ReportContext
    from repowise.core.report.sections.bus_factor import collect as bus_factor

    checks = await bus_factor(ReportContext(ctx.session, ctx.repository_id, ctx.repo_root))
    figures = ((checks[0].get("data") or {}).get("components") or {}) if checks else {}
    known = [f for f in figures.values() if f.get("basis") != "unavailable"]
    single = sum(1 for f in known if f.get("value") == 1)
    if not single:
        return Collected()
    item = Item(
        id=item_id(SECTION, "bus_factor", ""),
        section=SECTION,
        rule="bus_factor",
        title_facts={"components": len(known), "bus_factor_1": single},
        locations=(),
        confidence="medium",
        basis="measured",
        severity="plan",
        effort="M",
        link=Link("ownership"),
        prompt=PromptRef("first_look_item"),
        fingerprint=fingerprint(len(known), single),
    )
    return Collected((item,), {"components": len(known)})


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

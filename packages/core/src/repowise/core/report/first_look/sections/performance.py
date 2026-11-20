"""Performance: causes the performance queue admits, from its persisted read model.

A row is listed when the queue judged it eligible (``queue_eligible``), its
cause may lead (``may_lead``, the one rule the queue, Fix first and the rank
read) and its file is production code. A cause a profile run ranked is
``measured`` and comes first; the rest are static (``inferred``).
"""

from __future__ import annotations

from repowise.core.analysis.actions.model import fingerprint, shared_priority
from repowise.core.analysis.health.biomarker_labels import biomarker_label
from repowise.core.analysis.health.rows import detail_map
from repowise.core.analysis.health.worth import execution_role, measured, perf_facets

from ..context import FirstLookContext
from ..model import (
    Collected,
    FirstLookSection,
    Item,
    Location,
    Member,
    PromptRef,
    file_link,
    first_look_sections,
    item_id,
)
from ._common import confidence, effort, short_symbol

SECTION = "performance"
#: Queue-eligible causes read; the queue keeps few, so this is a guard, not a cut.
_READ_LIMIT = 500


async def collect(ctx: FirstLookContext) -> Collected:
    from repowise.core.persistence.crud.analysis.performance import (
        list_performance_opportunities,
    )

    rows, _ = await list_performance_opportunities(
        ctx.session, ctx.repository_id, queue_eligible=True, limit=_READ_LIMIT
    )
    _, production_total = await list_performance_opportunities(
        ctx.session, ctx.repository_id, contexts=frozenset({"production"}), limit=1
    )
    items = []
    for row in rows:
        details = detail_map(row)
        path = row.file_path
        if not details.get("may_lead") or not ctx.is_production(path):
            continue
        facets = perf_facets(row)
        steps = (details.get("plan") or {}).get("steps") or []
        line = next((s.get("line") for s in steps if s.get("line")), None)
        symbol = short_symbol(row.intervention_symbol)
        bucket = effort((details.get("plan") or {}).get("effort_bucket"))
        level = confidence(facets.get("actionability_confidence") or row.evidence_confidence)
        runtime = measured(facets)
        items.append(
            Item(
                id=item_id(SECTION, row.biomarker_type, path, symbol),
                section=SECTION,
                rule=row.biomarker_type,
                title_facts={
                    "symbol": symbol,
                    "marker": biomarker_label(row.biomarker_type),
                    "role": execution_role(row),
                    "call_sites": row.affected_call_sites_total,
                    "steps": len(steps),
                },
                locations=(Location(path, line, symbol),),
                locations_total=1,
                confidence=level,
                basis="measured" if runtime else "inferred",
                severity="plan",
                effort=bucket,
                link=file_link(path, line),
                prompt=PromptRef("perf_opportunity", row.opportunity_id),
                fingerprint=fingerprint(row.biomarker_type, level, runtime, bucket, len(steps)),
                origin=ctx.origin(path),
                priority=shared_priority(row.queue_value, "medium", level, bucket),
                members=(Member("performance", row.opportunity_id),),
            )
        )
    return Collected(tuple(items), {"production_causes": production_total})


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

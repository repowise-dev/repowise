"""Code health: the head of the Fix-first queue and the lowest-scoring files.

Items are the queue's due work (``DUE_TIERS``) that is not a performance fix
(the Performance section lists those from their own read model), plus the
three lowest-health production files. The headline is the measured
association every health surface shows (``compute_defect_accuracy``): how many
of the lowest-health files had a recent bug fix, against the base rate.
"""

from __future__ import annotations

import logging
from typing import Any

from repowise.core.analysis.actions.model import fingerprint, shared_priority
from repowise.core.analysis.health.grading import band_for
from repowise.core.analysis.health.queue.order import DUE_TIERS

from ..context import FirstLookContext
from ..model import (
    Collected,
    FirstLookSection,
    Item,
    Link,
    Location,
    Member,
    PromptRef,
    file_link,
    first_look_sections,
    item_id,
)
from ._common import confidence, effort, short_symbol

SECTION = "health"
logger = logging.getLogger(__name__)
#: Lowest-health production files listed beside the queue.
LOWEST_FILES = 3


def _sizes(ctx: FirstLookContext, path: str) -> dict[str, int | None]:
    """The numbers a health row is read by, from the rows the section reads:
    the file's largest function CCN and code lines (its health metrics), the
    files importing it and its commits in 90 days (the facts). ``None`` when
    the index has no figure."""
    metric = ctx.metric(path)
    facts = ctx.facts.files.get(path)
    return {
        "ccn": metric.max_ccn if metric is not None else None,
        "nloc": metric.nloc if metric is not None else None,
        "importers": facts.dependents if facts is not None else None,
        "changes_90d": facts.commits_90d if facts is not None else None,
    }


def _members(fix: Any) -> tuple[Member, ...]:
    """The stored rows a Fix-first item stands for: a refactor's opportunity
    (its plans follow it), else its plans; a finding's health findings."""
    source = fix.source
    if fix.kind == "refactor":
        if source.opportunity_id:
            return (Member("refactoring_opportunity", source.opportunity_id),)
        return tuple(Member("refactoring_plan", p) for p in source.plan_ids)
    return tuple(Member("health_finding", f) for f in source.finding_ids)


def _fix_items(ctx: FirstLookContext, queue: Any) -> list[Item]:
    items = []
    for fix in queue.items:
        path = fix.target.file_path
        if fix.tier not in DUE_TIERS or fix.kind == "perf_fix" or not ctx.is_production(path):
            continue
        symbol = short_symbol(fix.target.symbol)
        level = confidence(fix.confidence.level)
        facts = {f.label: f.value for f in fix.facts}
        items.append(
            Item(
                id=item_id(SECTION, fix.kind, path, symbol),
                section=SECTION,
                rule=fix.kind,
                title_facts={
                    "symbol": symbol,
                    "title": fix.title,
                    "facts": facts,
                    "verify": fix.verify.command,
                    **_sizes(ctx, path),
                },
                locations=(Location(path, fix.target.line_start, symbol),),
                locations_total=1,
                confidence=level,
                basis="inferred",
                severity="plan",
                effort=effort(fix.effort.bucket),
                link=file_link(path, fix.target.line_start),
                prompt=PromptRef("fix_item", fix.id),
                fingerprint=fingerprint(fix.title, *sorted(facts.items())),
                origin=ctx.origin(path),
                priority=shared_priority(fix.value, "medium", level, fix.effort.bucket),
                # The queue's own order breaks ties inside a priority.
                weight=float(-fix.rank),
                members=_members(fix),
            )
        )
    return items


def _lowest_files(ctx: FirstLookContext) -> list[Item]:
    items = []
    for m in ctx.metrics:
        if len(items) >= LOWEST_FILES:
            break
        if m.score is None or not ctx.is_production(m.file_path):
            continue
        score = round(float(m.score), 1)
        items.append(
            Item(
                id=item_id(SECTION, "lowest_health_file", m.file_path),
                section=SECTION,
                rule="lowest_health_file",
                title_facts={"score": score, "band": band_for(score), **_sizes(ctx, m.file_path)},
                locations=(Location(m.file_path),),
                locations_total=1,
                confidence="medium",
                basis="inferred",
                severity="plan",
                effort="M",
                link=Link("health_file", {"path": m.file_path}),
                prompt=PromptRef("first_look_item"),
                fingerprint=fingerprint(band_for(score)),
                origin=ctx.origin(m.file_path),
            )
        )
    return items


async def _defect_accuracy(ctx: FirstLookContext) -> dict | None:
    from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
    from repowise.core.persistence.crud.analysis.health.findings import (
        get_deduction_by_path,
        get_health_findings,
    )

    # The labels, read as the health overview reads them: every open row.
    prior = await get_health_findings(
        ctx.session, ctx.repository_id, biomarker_type="prior_defect", include_unverified=True
    )
    found = compute_defect_accuracy(
        ctx.metrics,
        prior,
        deductions=await get_deduction_by_path(ctx.session, ctx.repository_id),
    )
    if found is None:
        return None
    keep = ("k", "hits", "precision", "base_rate", "lift", "window_days", "scored_files")
    return {k: found[k] for k in keep}


async def collect(ctx: FirstLookContext) -> Collected:
    from repowise.core.persistence.crud.analysis.fix_first import load_fix_first

    ctx.require("files")
    queue = await load_fix_first(ctx.session, ctx.repository_id, limit=None, verify=False)
    items = _fix_items(ctx, queue) + _lowest_files(ctx)
    facts: dict = {}
    try:
        async with ctx.session.begin_nested():
            accuracy = await _defect_accuracy(ctx)
        if accuracy is not None:
            facts["defect_accuracy"] = {**accuracy, "basis": "measured"}
    except Exception:  # the headline is optional; the items stand without it
        logger.warning("repo report: defect accuracy unavailable", exc_info=True)
    return Collected(tuple(items), facts)


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

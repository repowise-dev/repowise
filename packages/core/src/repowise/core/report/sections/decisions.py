"""Governing decisions per component, with the ones gone stale."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import (
    ReportContext,
    Section,
    capped,
    check,
    component_summary,
    report_sections,
)

_METHOD = (
    "accepted decisions at a governing currency whose recorded files fall in the component; "
    "stale at staleness 0.5 or above"
)


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    from sqlalchemy import func, select

    from repowise.core.events.snapshot import governing_decisions
    from repowise.core.persistence.models import DecisionRecord

    recorded = await ctx.session.scalar(
        select(func.count()).where(DecisionRecord.repository_id == ctx.repository_id)
    )
    if not recorded:
        return [
            check(
                "decisions",
                reason="no decision is recorded for this repository",
                method=_METHOD,
                basis="unresolved",
            )
        ]
    governing, stale = await governing_decisions(ctx.session, ctx.repository_id)
    titles = {d: t for pairs in governing.values() for d, t in pairs}
    out = [
        check(
            "decisions",
            reason=(
                f"{len(titles)} governing decision(s) of {recorded} recorded, {len(stale)} stale"
                + ("; zero may mean none were recorded for these files" if not titles else "")
            ),
            details=capped([f"stale: {d} {titles[d]}" for d in sorted(stale)]),
            method=_METHOD,
            basis="declared",
            data={"governing": len(titles), "recorded": recorded, "stale": sorted(stale)},
        )
    ]
    found = await ctx.components()
    figures: dict[str, dict[str, Any]] = {}
    outcomes = {"with stale decisions": 0, "governed, none stale": 0, "no governing decision": 0}
    quiet: list[str] = []
    noted: list[dict[str, Any]] = []
    for comp in found.resolved.components:
        paths = found.resolved.files.get(comp.name, ())
        ids = sorted({d for p in paths for d, _ in governing.get(p, ())})
        gone = [d for d in ids if d in stale]
        figures[comp.name] = {"governing": ids, "stale": gone}
        if not gone:
            outcomes["governed, none stale" if ids else "no governing decision"] += 1
            if ids:
                quiet.append(comp.name)
            continue
        outcomes["with stale decisions"] += 1
        noted.append(
            check(
                f"decisions:{comp.name}",
                reason=f"{len(ids)} governing decision(s), {len(gone)} stale",
                details=capped(
                    [f"{d}{' (stale)' if d in stale else ''}: {titles[d]}" for d in ids]
                ),
                method=_METHOD,
                basis="declared",
                data=figures[comp.name],
            )
        )
    out.append(
        component_summary(
            "decisions:components",
            outcomes=outcomes,
            quiet=quiet,
            method=_METHOD,
            basis="declared",
            data={"components": figures},
        )
    )
    return out + noted


report_sections.register(
    "decisions",
    Section(
        title="Governing decisions",
        summary="Recorded decisions that bind each component's files, and which are stale.",
        collect=collect,
        order=70,
    ),
)

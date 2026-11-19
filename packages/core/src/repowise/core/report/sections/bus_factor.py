"""Bus-factor exposure per component. Owners are counted, never named."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import (
    ReportContext,
    Section,
    check,
    component_summary,
    report_sections,
)

_METHOD = "fewest primary owners who together own over half the owned files"
#: A bus factor at or below this is counted and named in the summary row.
_LOW = 2


def _details(bus: dict[str, Any], comp: Any) -> list[str]:
    return [
        f"{bus['owned_files']} of {bus['file_count']} file(s) have a known primary owner",
        f"{bus['single_owner_files']} file(s) have a single owner",
        *([f"criticality: {comp.criticality}"] if comp.criticality else []),
        *([f"tags: {', '.join(comp.tags)}"] if comp.tags else []),
        *([bus["note"]] if bus.get("note") else []),
    ]


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    found = await ctx.components()
    rollups = await ctx.rollups()
    declared = {c.name: c for c in found.resolved.components}
    out: list[dict[str, Any]] = []
    figures: dict[str, dict[str, Any]] = {}
    outcomes = {"bus factor 1": 0, "bus factor 2": 0, "3 or more": 0, "unresolved": 0}
    quiet: list[str] = []
    low: dict[str, list[str]] = {"bus factor 1": [], "bus factor 2": []}
    for name, rolled in sorted((n, r) for n, r in rollups.items() if n is not None):
        bus = figures[name] = rolled["bus_factor"]
        if bus["basis"] == "unavailable":
            outcomes["unresolved"] += 1
            out.append(
                check(
                    f"bus-factor:{name}", reason=bus["reason"], method=_METHOD, basis="unresolved"
                )
            )
            continue
        if bus["value"] > _LOW:
            outcomes["3 or more"] += 1
            quiet.append(name)
            continue
        label = f"bus factor {bus['value']}"
        outcomes[label] += 1
        comp = declared.get(name)
        # Only a component the catalog marks (criticality or tags) gets its own
        # item; every other low one is counted and named in the summary row.
        if comp is None or not (comp.criticality or comp.tags):
            low[label].append(name)
            continue
        out.append(
            check(
                f"bus-factor:{name}",
                reason=f"bus factor {bus['value']}",
                details=_details(bus, comp),
                method=bus["method"],
                basis=bus["basis"],
                data=bus,
            )
        )
    out.insert(
        0,
        component_summary(
            "bus-factor",
            outcomes=outcomes,
            quiet=quiet,
            named=low,
            method=_METHOD,
            basis="measured",
            data={"components": figures},
        ),
    )
    return out


report_sections.register(
    "bus-factor",
    Section(
        title="Bus-factor exposure",
        summary="How few people hold most of each component's files; counted, never named.",
        collect=collect,
        order=30,
    ),
)

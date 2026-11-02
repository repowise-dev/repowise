"""Hotspots: files git history flags for high recent churn, per component."""

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

_METHOD = "files git history flags as hotspots (high recent churn), from the component rollups"


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    rollups = await ctx.rollups()
    whole = (rollups.get(None) or {}).get("hotspots") or {"basis": "unavailable"}
    if whole["basis"] != "measured":
        return [
            check(
                "hotspots",
                reason=whole.get("reason", "no git history indexed"),
                method=_METHOD,
                basis="unresolved",
            )
        ]
    components = sorted((n, r["hotspots"]) for n, r in rollups.items() if n is not None)
    hot = sorted(
        ((n, h) for n, h in components if h["basis"] == "measured" and h["count"]),
        key=lambda kv: (-kv[1]["count"], kv[0]),
    )
    unresolved = [(n, h) for n, h in components if h["basis"] != "measured"]
    quiet = [n for n, h in components if h["basis"] == "measured" and not h["count"]]
    out = [
        check(
            "hotspots",
            reason=f"{whole['count']} hotspot file(s) across {len(hot)} component(s)",
            details=capped([f"{name}: {h['count']}" for name, h in hot]),
            method=_METHOD,
            basis="measured",
            data={"count": whole["count"], "top": whole["top"]},
        ),
        component_summary(
            "hotspots:components",
            outcomes={
                "with hotspots": len(hot),
                "no git history": len(unresolved),
                "no hotspots": len(quiet),
            },
            quiet=quiet,
            method=_METHOD,
            basis="measured",
            data={"components": dict(components)},
        ),
    ]
    for name, h in hot:
        rest = h["count"] - len(h["top"])
        out.append(
            check(
                f"hotspots:{name}",
                reason=f"{h['count']} hotspot file(s)",
                # The rollup keeps the top files by hotspot score; the rest are counted.
                details=[*h["top"], *([f"and {rest} more"] if rest > 0 else [])],
                method=_METHOD,
                basis="measured",
                data=h,
            )
        )
    for name, h in unresolved:
        out.append(
            check(f"hotspots:{name}", reason=h["reason"], method=_METHOD, basis="unresolved")
        )
    return out


report_sections.register(
    "hotspots",
    Section(
        title="Hotspots",
        summary="Files with high recent churn, counted per component.",
        collect=collect,
        order=20,
    ),
)

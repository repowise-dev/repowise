"""Test reach: line coverage where a report was ingested (measured), and
which production files a test reaches statically (inferred), kept apart."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import (
    ReportContext,
    Section,
    check,
    component_summary,
    report_sections,
)


def _items(component: str | None, reach: dict[str, Any]) -> list[dict[str, Any]]:
    """The items for one component, or the whole repository when *component* is ``None``."""
    suffix = f":{component}" if component is not None else ""
    if reach["basis"] == "unavailable":
        return [
            check(
                f"test-reach{suffix}",
                reason=reach["reason"],
                method="test pairing from health analysis",
                basis="unresolved",
            )
        ]
    out = [
        check(
            f"test-reach{suffix}",
            score=reach["share"] * 10,
            reason=(
                f"{reach['reached_files']} of {reach['production_files']} production file(s) "
                "reached by a test"
            ),
            method=reach["method"],
            basis="inferred",
            data={k: v for k, v in reach.items() if k != "line_coverage"},
        )
    ]
    measured = reach.get("line_coverage")
    if measured:
        out.append(
            check(
                f"line-coverage{suffix}",
                score=measured["pct"] / 10,
                reason=(
                    f"{measured['pct']}% line coverage over {measured['measured_files']} file(s)"
                ),
                method=measured["method"],
                basis="measured",
                data=measured,
            )
        )
    return out


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    rollups = await ctx.rollups()
    whole = rollups.get(None)
    if whole is None:
        return []
    out = _items(None, whole["test_reach"])
    if whole["test_reach"]["basis"] != "unavailable" and "line_coverage" not in whole["test_reach"]:
        out.append(
            check(
                "line-coverage",
                reason="no coverage report is ingested; `repowise coverage add` ingests one",
                method="NLOC-weighted line coverage from ingested coverage reports",
                basis="unresolved",
            )
        )
    figures: dict[str, dict[str, Any]] = {}
    outcomes = {"under half reached": 0, "half or more reached": 0, "unresolved": 0}
    quiet: list[str] = []
    noted: list[dict[str, Any]] = []
    for name, rolled in sorted((n, r) for n, r in rollups.items() if n is not None):
        reach = figures[name] = rolled["test_reach"]
        if reach["basis"] == "unavailable":
            outcomes["unresolved"] += 1
            noted.extend(_items(name, reach))
            continue
        outcomes["under half reached" if reach["share"] < 0.5 else "half or more reached"] += 1
        quiet.append(name)
    out.append(
        component_summary(
            "test-reach:components",
            outcomes=outcomes,
            quiet=quiet,
            method="per-component test reach and line coverage; figures in data",
            basis="inferred",
            data={"components": figures},
        )
    )
    out.extend(noted)
    return out


report_sections.register(
    "test-reach",
    Section(
        title="Test reach",
        summary="Measured line coverage and inferred test reach, reported separately.",
        collect=collect,
        order=50,
    ),
)

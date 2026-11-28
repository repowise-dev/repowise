"""Tests and coverage: measured coverage only.

The section's facts are the stored coverage totals. Its items are the
fragile files (the ``fragile_file`` rule's own predicate: bug fixes keep
landing in a busy file) that have measured coverage, lowest coverage first.
Inferred reach and impacted-test lists stay out.
"""

from __future__ import annotations

from repowise.core.analysis.actions.model import fingerprint
from repowise.core.analysis.actions.rules.code import is_fragile

from ..context import FirstLookContext
from ..model import (
    Collected,
    FirstLookSection,
    Item,
    Location,
    PromptRef,
    file_link,
    first_look_sections,
    item_id,
)

SECTION = "tests"
#: Fragile files with measured coverage listed.
RISKIEST_FILES = 3


async def collect(ctx: FirstLookContext) -> Collected:
    from repowise.core.persistence.crud.analysis.coverage import get_coverage_summary

    ctx.require("coverage")
    summary = await get_coverage_summary(ctx.session, ctx.repository_id)
    if not summary.get("file_count"):
        return Collected()
    facts = {
        "files_measured": summary["file_count"],
        "line_pct": summary.get("line_coverage_pct"),
        "branch_pct": summary.get("branch_coverage_pct"),
        "formats": list(summary.get("source_formats") or ()),
        "partial": bool(summary.get("mapping_partial")),
        "freshness": summary.get("freshness"),
    }
    fragile = sorted(
        (
            f
            for f in ctx.facts.files.values()
            if f.line_coverage_pct is not None
            and ctx.is_production(f.path)
            and is_fragile(f, ctx.rules)
        ),
        key=lambda f: (f.line_coverage_pct, -f.fix_commits_90d, f.path),
    )[:RISKIEST_FILES]
    items = tuple(
        Item(
            id=item_id(SECTION, "fragile_measured_coverage", f.path),
            section=SECTION,
            rule="fragile_measured_coverage",
            title_facts={
                "line_pct": round(f.line_coverage_pct, 1),
                "fix_commits_90d": f.fix_commits_90d,
                "commits_90d": f.commits_90d,
            },
            locations=(Location(f.path),),
            locations_total=1,
            confidence="high",
            basis="measured",
            severity="plan",
            effort="M",
            link=file_link(f.path),
            prompt=PromptRef("first_look_item"),
            fingerprint=fingerprint(round(f.line_coverage_pct), f.fix_commits_90d // 5),
            origin=ctx.origin(f.path),
        )
        for f in fragile
    )
    return Collected(items, facts, stands_alone=True)


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

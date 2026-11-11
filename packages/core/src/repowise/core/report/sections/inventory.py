"""Software inventory: each component's declared owner teams, languages, entry
points and the third-party packages it imports (:mod:`repowise.core.inventory.components`).

Like the other per-component sections it opens with one summary row; a
component gets an item of its own only when no team is declared for it, and the
JSON ``data`` keeps every component's full row.
"""

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


def _details(comp: dict[str, Any]) -> list[str]:
    langs = ", ".join(f"{lang} {n}" for lang, n in comp["languages"].items())
    return capped(
        [
            *([f"languages: {langs}"] if langs else []),
            *(f"entry point: {p}" for p in comp["entry_points"]),
            *(f"package: {p['package_key']} ({p['files']} file(s))" for p in comp["packages"]),
            *(f"test-only package: {k}" for k in comp.get("test_only_packages", [])),
        ]
    )


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    from repowise.core.inventory.components import component_inventory

    inv = await component_inventory(ctx.session, ctx.repository_id, ctx.repo_root)
    method = inv["method"]
    packages = inv["packages"]
    comps = inv["components"]
    unowned = [c for c in comps if not c["owners"]["teams"] and c["file_count"]]
    owned = [c["name"] for c in comps if c["owners"]["teams"]]
    empty = [c["name"] for c in comps if not c["file_count"]]
    comp_method = (
        f"owners: {method['owners']}; languages: {method['languages']}; "
        f"entry points: {method['entry_points']}; packages: {method['packages']}"
    )
    out = [
        check(
            "inventory",
            reason=(
                f"{len(comps)} component(s); {packages['declared']} declared package(s), "
                f"{packages['imported']} imported through the import graph"
            ),
            details=capped(
                [
                    *(f"declared, no linked import: {k}" for k in packages["declared_not_imported"]),
                    *(f"team directory: {issue}" for issue in inv["directory_issues"]),
                ]
            ),
            method=f"{method['packages']}; {method['declared_not_imported']}",
            basis="measured",
            data={"packages": packages, "unassigned_files": inv["unassigned_files"]},
        ),
        component_summary(
            "inventory:components",
            outcomes={
                "with a declared owner team": len(owned),
                "no declared owner team": len(unowned),
                "no files": len(empty),
            },
            quiet=owned,
            named={"no files": empty},
            method=comp_method,
            basis="declared",
            data={"components": {c["name"]: c for c in comps}},
        ),
    ]
    for comp in unowned:
        out.append(
            check(
                f"inventory:{comp['name']}",
                reason=(
                    f"{comp['file_count']} file(s) and no declared owner team; "
                    f"{len(comp['entry_points'])} entry point(s), "
                    f"{len(comp['packages'])} third-party package(s)"
                ),
                details=_details(comp),
                method=comp_method,
                basis="declared",
                data=comp,
            )
        )
    return out


report_sections.register(
    "inventory",
    Section(
        title="Software inventory",
        summary="Each component's owner teams, languages, entry points and third-party packages.",
        collect=collect,
        order=15,
    ),
)

"""Contracts each component provides, with consumer links in other repos."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import ReportContext, Section, capped, check, report_sections

_METHOD = "provider contracts from the workspace contract store and their cross-repo consumer links"


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    from repowise.core.events.snapshot import provided_contracts

    contracts = provided_contracts(ctx.repo_root, ctx.head_commit)
    if contracts is None:
        return [
            check(
                "contracts",
                reason=(
                    "contracts are read from a workspace's contract store extracted at this "
                    "repository's indexed commit, and none is available"
                ),
                method=_METHOD,
                basis="unresolved",
            )
        ]
    found = await ctx.components()
    out = []
    total = 0
    quiet: list[str] = []
    for comp in found.resolved.components:
        provided: dict[str, int] = {}
        for path in found.resolved.files.get(comp.name, ()):
            for cid, consumers in contracts.get(path, {}).items():
                provided[cid] = provided.get(cid, 0) + consumers
        if not provided:
            quiet.append(comp.name)
            continue
        total += len(provided)
        consumed = sum(1 for n in provided.values() if n)
        out.append(
            check(
                f"contracts:{comp.name}",
                reason=f"{len(provided)} provided contract(s), {consumed} with consumers elsewhere",
                details=capped(
                    [f"{cid}: {n} consumer link(s)" for cid, n in sorted(provided.items())]
                ),
                method=_METHOD,
                basis="measured",
                data={"provided": dict(sorted(provided.items()))},
            )
        )
    out.insert(
        0,
        check(
            "contracts",
            reason=(
                f"{total} provided contract(s) detected across {len(out)} component(s)"
                + ("; zero may mean none were detected, not that none exist" if not total else "")
            ),
            details=[f"none detected: {', '.join(quiet)}"] if quiet else [],
            method=_METHOD,
            basis="measured",
            data={"provided": total},
        ),
    )
    return out


report_sections.register(
    "contracts",
    Section(
        title="Contracts",
        summary="Contracts each component provides and how many other repos consume them.",
        collect=collect,
        order=60,
    ),
)

"""Contracts each component provides, with consumer links in other repos."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from repowise.core.report.model import ReportContext, Section, capped, check, report_sections

#: The row for provider files outside every component.
_OUTSIDE = "(no component)"
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
    outside = 0
    # Distinct ids: a contract served from files in two components is one.
    every: set[str] = set()
    quiet: list[str] = []
    rows = [(c.name, found.resolved.files.get(c.name, ())) for c in found.resolved.components]
    owned = {path for _name, paths in rows for path in paths}
    # Provider files no component claims (root files of a divided repo, or
    # files the index left out) still count, under a row of their own.
    rows.append((_OUTSIDE, [path for path in contracts if path not in owned]))
    for name, paths in rows:
        provided = _provided(paths, contracts)
        if not provided:
            if name != _OUTSIDE:
                quiet.append(name)
            continue
        every.update(provided)
        if name == _OUTSIDE:
            outside = len(provided)
        consumed = sum(1 for n in provided.values() if n)
        out.append(
            check(
                f"contracts:{name}",
                reason=f"{len(provided)} provided contract(s), {consumed} with consumers elsewhere",
                details=capped(
                    [f"{cid}: {n} consumer link(s)" for cid, n in sorted(provided.items())]
                ),
                method=_METHOD,
                basis="measured",
                data={"provided": dict(sorted(provided.items()))},
            )
        )
    total = len(every)
    out.insert(
        0,
        check(
            "contracts",
            reason=(
                f"{total} provided contract(s) detected across "
                f"{len(out) - bool(outside)} component(s)"
                + (f", {outside} outside every component" if outside else "")
                + ("; zero may mean none were detected, not that none exist" if not total else "")
            ),
            details=[f"none detected: {', '.join(quiet)}"] if quiet else [],
            method=_METHOD,
            basis="measured",
            data={"provided": total},
        ),
    )
    return out


def _provided(paths: Iterable[str], contracts: dict[str, dict[str, int]]) -> dict[str, int]:
    """Consumer links per contract id provided in *paths*."""
    provided: dict[str, int] = {}
    for path in paths:
        for cid, consumers in contracts.get(path, {}).items():
            provided[cid] = provided.get(cid, 0) + consumers
    return provided


report_sections.register(
    "contracts",
    Section(
        title="Contracts",
        summary="Contracts each component provides and how many other repos consume them.",
        collect=collect,
        order=60,
    ),
)

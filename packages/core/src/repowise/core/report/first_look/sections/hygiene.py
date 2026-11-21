"""Repo hygiene: import cycles and vendored code in the code tree.

Cycles are the stored file-level strongly connected components of two to four
production files (``graph_node_membership``). Vendored files are the stored
origin ``vendored`` outside a directory the index already skips as vendor
code. Here the origin is the finding, so it is the one section that lists
code that is not production.
"""

from __future__ import annotations

from repowise.core.analysis.actions.model import fingerprint
from repowise.core.fs_walk import POLICY_BLOCKED_DIRS

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
    locations,
)

SECTION = "hygiene"
#: Cycle sizes listed: a larger one is architecture work, not a quick fix.
CYCLE_SIZES = range(2, 5)


async def _cycles(ctx: FirstLookContext) -> list[Item]:
    from repowise.core.persistence.crud.graph import get_scc_members

    items = []
    for members in (await get_scc_members(ctx.session, ctx.repository_id)).values():
        if len(members) not in CYCLE_SIZES or not all(ctx.is_production(m) for m in members):
            continue
        items.append(
            Item(
                id=item_id(SECTION, "import_cycle", members[0], ",".join(members)),
                section=SECTION,
                rule="import_cycle",
                title_facts={"files": len(members)},
                locations=tuple(Location(m) for m in members),
                locations_total=len(members),
                confidence="high",
                basis="inferred",
                severity="plan",
                effort="M",
                link=file_link(members[0]),
                prompt=PromptRef("first_look_item"),
                fingerprint=fingerprint(*members),
                origin="production",
            )
        )
    return sorted(items, key=lambda i: i.locations[0].path)


def _vendored(ctx: FirstLookContext) -> list[Item]:
    found = sorted(
        path
        for path, origin in ctx.origins.items()
        if origin == "vendored" and not POLICY_BLOCKED_DIRS.intersection(path.split("/")[:-1])
    )
    if not found:
        return []
    locs, total = locations([Location(p) for p in found])
    return [
        Item(
            id=item_id(SECTION, "vendored_in_tree", ""),
            section=SECTION,
            rule="vendored_in_tree",
            title_facts={"files": total},
            locations=locs,
            locations_total=total,
            confidence="high",
            basis="inferred",
            severity="plan",
            effort="S",
            link=file_link(found[0]),
            prompt=PromptRef("first_look_item"),
            fingerprint=fingerprint(*found),
            origin="vendored",
        )
    ]


async def collect(ctx: FirstLookContext) -> Collected:
    return Collected(tuple(await _cycles(ctx) + _vendored(ctx)))


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

"""Dead code: the two tiers a hand audit found all true.

``safe_to_delete`` exports are the rows ``repowise next`` batches (open,
re-derived safe by ``is_safe``, served, outside tests, review-only kinds out),
one item per file. Unreachable files are listed only at the audited
confidence and labelled check-then-delete. Everything else is one count.
Production code only: scripts, docs examples, data and tests stay out.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from repowise.core.analysis.actions.model import fingerprint, shared_priority
from repowise.core.analysis.actions.rules.hygiene import _standalone, dead_code_batch

from ..context import FirstLookContext, UnavailableError
from ..model import (
    Collected,
    FirstLookSection,
    Item,
    Link,
    Location,
    Member,
    PromptRef,
    dead_code_ref,
    file_link,
    first_look_sections,
    item_id,
    locations,
)

SECTION = "dead_code"
#: Unreachable files listed: the confidence rung (a file untouched for six
#: months, nothing reaching it) whose sample a hand audit found 8 of 8 dead.
#: Lower rungs (0.6 at 85%, 0.4 at 72%) are counted, never listed.
UNREACHABLE_MIN_CONFIDENCE = 0.9


def _priority() -> float:
    """What ``dead_code_batch`` ranks a deletion at: low severity, high confidence, small."""
    return shared_priority(None, "low", "high", "S")


#: Finding ids one kind lookup reads per query (SQLite's bound-parameter floor).
_ID_CHUNK = 500


async def _kinds(ctx: FirstLookContext, ids: list[str]) -> dict[str, str]:
    """Each finding's kind, the part of its identity the facts leave out."""
    from repowise.core.persistence.models import DeadCodeFinding

    out: dict[str, str] = {}
    for n in range(0, len(ids), _ID_CHUNK):
        rows = await ctx.session.execute(
            select(DeadCodeFinding.id, DeadCodeFinding.kind).where(
                DeadCodeFinding.id.in_(ids[n : n + _ID_CHUNK])
            )
        )
        out.update(dict(rows.all()))
    return out


async def _exports(ctx: FirstLookContext) -> list[Item]:
    by_file: dict[str, list] = defaultdict(list)
    for d in ctx.facts.dead:
        if ctx.is_production(d.file_path) and not _standalone(d.file_path):
            by_file[d.file_path].append(d)
    kinds = await _kinds(ctx, [d.finding_id for found in by_file.values() for d in found])
    items = []
    for path, found in sorted(by_file.items()):
        found.sort(key=lambda d: (d.symbol or "", d.finding_id))
        locs, total = locations([Location(path, None, d.symbol) for d in found])
        lines = sum(d.lines for d in found)
        items.append(
            Item(
                id=item_id(SECTION, "safe_to_delete", path),
                section=SECTION,
                rule="safe_to_delete",
                title_facts={"symbols": len(found), "lines": lines},
                locations=locs,
                locations_total=total,
                confidence="high",
                basis="inferred",
                severity="plan",
                effort="S",
                link=Link("dead_code"),
                # Per file: the batch's own prompt lists every file at once.
                prompt=PromptRef("first_look_item"),
                fingerprint=fingerprint(*sorted(d.symbol or "" for d in found)),
                origin=ctx.origin(path),
                priority=_priority(),
                weight=float(lines),
                members=tuple(
                    Member("dead_code", dead_code_ref(kinds.get(d.finding_id, ""), d.symbol, path))
                    for d in found
                ),
            )
        )
    return items


async def _unreachable(ctx: FirstLookContext) -> list[Item]:
    from repowise.core.analysis.dead_code.serving import serve_findings
    from repowise.core.analysis.finding_registry import excluded_types, is_served
    from repowise.core.persistence.models import DeadCodeFinding

    d = DeadCodeFinding
    rows = (
        await ctx.session.execute(
            select(d.id, d.file_path, d.kind, d.lines, d.confidence, d.safe_to_delete).where(
                d.repository_id == ctx.repository_id,
                d.status == "open",
                d.kind == "unreachable_file",
            )
        )
    ).all()
    excluded = excluded_types()
    items = []
    for f in serve_findings(list(rows), None, None):
        path = f.file_path
        if (
            (f.confidence or 0) < UNREACHABLE_MIN_CONFIDENCE
            or not is_served("dead_code", f.kind, path, excluded)
            or not ctx.is_production(path)
        ):
            continue
        items.append(
            Item(
                id=item_id(SECTION, "unreachable_file", path),
                section=SECTION,
                rule="unreachable_file",
                title_facts={"lines": f.lines or 0, "check_first": True},
                locations=(Location(path),),
                locations_total=1,
                confidence="medium",
                confidence_score=float(f.confidence),
                basis="inferred",
                severity="plan",
                effort="S",
                link=file_link(path),
                prompt=PromptRef("first_look_item"),
                fingerprint=fingerprint(f.confidence),
                origin=ctx.origin(path),
                priority=_priority(),
                weight=float(f.lines or 0),
                members=(Member("dead_code", dead_code_ref(f.kind, None, path)),),
            )
        )
    return sorted(items, key=lambda i: i.locations[0].path)


async def collect(ctx: FirstLookContext) -> Collected:
    from repowise.core.persistence.crud.analysis.dead_code import get_dead_code_summary

    outcome = dead_code_batch(ctx.facts, ctx.rules)
    if outcome.status == "unavailable":
        raise UnavailableError(outcome.reason)
    # Below the batch floor ``next`` lists no deletion, and neither does this.
    batch = outcome.actions[0] if outcome.actions else None
    items = (await _exports(ctx) if batch else []) + await _unreachable(ctx)
    summary = await get_dead_code_summary(ctx.session, ctx.repository_id)
    listed = sum(i.locations_total for i in items)
    return Collected(
        tuple(items),
        {
            "safe_exports": sum(i.locations_total for i in items if i.rule == "safe_to_delete"),
            "unreachable_files": sum(1 for i in items if i.rule == "unreachable_file"),
            "open_findings": summary["total_findings"],
            "not_listed": max(summary["total_findings"] - listed, 0),
            # ``render_action`` on this id is the whole-batch "delete them all" prompt.
            "batch_action_id": batch.action_id if batch else None,
        },
    )


first_look_sections.register(SECTION, FirstLookSection(SECTION, collect))

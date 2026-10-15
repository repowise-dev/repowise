"""Change outcomes: merged changes a later commit reverted or named as the
cause of what it fixed, in that commit's own words, rolled up per component.

Read from the checklists already kept on Change Records, never built here: a
change's outcomes are read the first time its checklist is opened (CLI, web,
REST, MCP). With none kept the section is left out. No link is inferred from
blame, and a file fix rate is never read as a cause.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
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
    "reverts that name the change and later commits that call it the cause in their own "
    "words, from the outcomes kept on each change's checklist (read when it was last opened, "
    "so they can predate the newest commits)"
)
_FLAGGED = "reverted or named as the cause by a later fix"


def _outcomes(raw: str | None) -> dict[str, Any] | None:
    """The kept ``outcomes`` item of one checklist, when it was read and known.

    Read through ``cached_evidence`` like every other reader of a kept
    checklist, so a stored status is read under the current rules.
    """
    from repowise.core.changes.collect import cached_evidence

    evidence = cached_evidence(SimpleNamespace(evidence_json=raw))
    item = evidence.item("outcomes") if evidence is not None else None
    return item.to_dict() if item is not None and item.status != "unknown" else None


def _files(files_json: str | None) -> list[str]:
    try:
        return [f["path"] for f in json.loads(files_json or "[]") if f.get("path")]
    except (TypeError, ValueError, AttributeError):
        return []


def _read_change(
    change_id: str, title: str, files_json: str | None, item: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """One change's figures and its detail lines, from its kept outcomes item."""
    links = [d for d in item.get("details") or () if d.get("kind") in ("revert", "fix_reference")]
    reverted = any(d["kind"] == "revert" and d.get("in_force") for d in links)
    named = [d for d in links if d["kind"] == "fix_reference"]
    lines = [f"reverted: {change_id} {title}"] if reverted else []
    for d in named:
        said = d.get("evidence", "")
        lines.append(f'{change_id} named by a later fix, in its words: "{said}"')
    figures = {"reverted": reverted, "cause_references": len(named), "files": _files(files_json)}
    return figures, lines


async def _kept_outcomes(ctx: ReportContext) -> tuple[int, dict[str, dict[str, Any]], list[str]]:
    """``(indexed changes, figures by change, detail lines)`` over the kept checklists."""
    from sqlalchemy import func, select

    from repowise.core.persistence.models import Change

    repo = Change.repository_id == ctx.repository_id
    total = await ctx.session.scalar(select(func.count()).where(repo))
    rows = (
        await ctx.session.execute(
            select(Change.change_id, Change.title, Change.files_json, Change.evidence_json)
            .where(repo, Change.evidence_json.is_not(None))
            .order_by(Change.merged_at.desc().nulls_last(), Change.change_id)
        )
    ).all()
    read: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    for change_id, title, files_json, evidence_json in rows:
        item = _outcomes(evidence_json)
        if item is not None:
            read[change_id], said = _read_change(change_id, title, files_json, item)
            lines += said
    return total, read, lines


async def _per_component(
    ctx: ReportContext, read: dict[str, dict[str, Any]], flagged: list[str]
) -> dict[str, dict[str, list[str]]]:
    """Each component's read and flagged changes, by the files they touched."""
    found = await ctx.components()
    owner_of: dict[str, list[str]] = {}
    for comp in found.resolved.components:
        for path in found.resolved.files.get(comp.name, ()):
            owner_of.setdefault(path, []).append(comp.name)
    figures: dict[str, dict[str, list[str]]] = {}
    for change_id, v in read.items():
        for name in sorted({n for p in v["files"] for n in owner_of.get(p, ())}):
            slot = figures.setdefault(name, {"read": [], "flagged": []})
            slot["read"].append(change_id)
            if change_id in flagged:
                slot["flagged"].append(change_id)
    return figures


async def collect(ctx: ReportContext) -> list[dict[str, Any]] | None:
    total, read, lines = await _kept_outcomes(ctx)
    if not read:
        return None
    flagged = [c for c, v in read.items() if v["reverted"] or v["cause_references"]]
    summary = check(
        "outcomes",
        reason=(
            f"{len(flagged)} of {len(read)} change(s) with outcomes read were {_FLAGGED}; "
            f"outcomes are read for {len(read)} of {total} indexed change(s)"
        ),
        details=capped(lines),
        method=_METHOD,
        basis="declared",
        data={
            "changes": total,
            "read": len(read),
            "flagged": flagged,
            "by_change": {
                c: {k: v[k] for k in ("reverted", "cause_references")} for c, v in read.items()
            },
        },
    )
    figures = await _per_component(ctx, read, flagged)
    marked = sorted(n for n, f in figures.items() if f["flagged"])
    quiet = sorted(n for n, f in figures.items() if not f["flagged"])
    components = component_summary(
        "outcomes:components",
        outcomes={f"with a change {_FLAGGED}": len(marked), "nothing to note": len(quiet)},
        quiet=quiet,
        named={_FLAGGED: marked},
        method=_METHOD,
        basis="declared",
        data={"components": figures},
    )
    return [summary, components]


report_sections.register(
    "outcomes",
    Section(
        title="Change outcomes",
        summary=(
            "Merged changes a later commit reverted or named as the cause of what it fixed, "
            "in its own words, per component."
        ),
        collect=collect,
        order=85,
    ),
)

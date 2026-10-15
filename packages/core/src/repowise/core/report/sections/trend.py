"""Compared with a quarter ago: what moved between the newest component
snapshot and the newest one at least 90 days older under the same stamp.

Reuses the event diff rules, so a move counts here exactly when it would
count as an event. Snapshots under different analyzer or parser stamps are
never compared.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from repowise.core.report.model import ReportContext, Section, capped, check, report_sections

QUARTER_DAYS = 90
_METHOD = (
    "the event diff rules, for the event kinds the repository has on, applied to two "
    f"stored component snapshots with the same analyzer and parser stamp, at least "
    f"{QUARTER_DAYS} days apart"
)


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    from repowise.core.events.diff import diff_snapshots
    from repowise.core.events.model import KINDS
    from repowise.core.events.store import GAP_STAMP, load_event_settings, run_facts
    from repowise.core.persistence.models import SnapshotRun

    runs = select(SnapshotRun).where(
        SnapshotRun.repository_id == ctx.repository_id,
        SnapshotRun.stamp.not_like(f"{GAP_STAMP}%"),
    )
    newest = await ctx.session.scalar(
        runs.order_by(SnapshotRun.taken_at.desc(), SnapshotRun.id.desc()).limit(1)
    )
    baseline = None
    if newest is not None:
        baseline = await ctx.session.scalar(
            runs.where(
                SnapshotRun.stamp == newest.stamp,
                SnapshotRun.taken_at <= newest.taken_at - timedelta(days=QUARTER_DAYS),
            )
            .order_by(SnapshotRun.taken_at.desc(), SnapshotRun.id.desc())
            .limit(1)
        )
    if baseline is None:
        return [
            check(
                "trend",
                reason=(
                    f"no stored snapshot from {QUARTER_DAYS} or more days before the newest "
                    "one under the same stamp"
                ),
                method=_METHOD,
                basis="unresolved",
            )
        ]
    before = await run_facts(ctx.session, baseline.id)
    after = await run_facts(ctx.session, newest.id)
    # The kinds this repository's events config turns on, so a move counts here
    # exactly when it would fire as an event (new_hotspot is off by default).
    kinds = load_event_settings(ctx.repo_root).kinds
    moved: dict[str, list[str]] = defaultdict(list)
    for kind, component, subject, _evidence in diff_snapshots(before, after):
        if kind in kinds:
            moved[kind].append(f"{component}: {subject}" if subject else component)
    span = f"{baseline.taken_at.date().isoformat()} to {newest.taken_at.date().isoformat()}"
    out = [
        check(
            "trend",
            reason=f"{sum(len(v) for v in moved.values())} move(s) from {span}",
            method=_METHOD,
            basis="measured",
            data={
                "from": baseline.taken_at.isoformat(),
                "to": newest.taken_at.isoformat(),
                "stamp": newest.stamp,
            },
        )
    ]
    for kind in sorted(moved):
        out.append(
            check(
                f"trend:{kind}",
                reason=f"{KINDS[kind].summary}: {len(moved[kind])}",
                details=capped(moved[kind]),
                method=_METHOD,
                basis="measured",
            )
        )
    return out


report_sections.register(
    "trend",
    Section(
        title="Compared with a quarter ago",
        summary="What moved per component since a snapshot at least 90 days older.",
        collect=collect,
        order=90,
    ),
)

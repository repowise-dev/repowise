"""The riskiest seams between services, ranked by a stated heuristic with its inputs shown."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import (
    Section,
    WorkspaceReportContext,
    capped,
    capped_names,
    check,
    report_sections,
)
from repowise.core.report.sections._estate import (
    evidence_note,
    git_history,
    missing_graph,
    unread,
)

_METHOD = (
    "a heuristic over the stored system graph, not a calibrated risk: each pair of services "
    "joined by an edge scores link confidence x reach (services a change to the depended-on "
    "side reaches, at most 3 hops) x (1 + co-change no contract explains); a pair joined by "
    "co-change alone scores co-change x reach. Co-change is read from the graph's co-change "
    "edges; it is not measured when a repo's index holds fewer than 2 commits"
)
#: Seams listed in the text; the JSON keeps every one.
_LISTED = 10


def _line(seam: Any) -> str:
    a, b = seam.services
    score = f"{seam.score:g}" if seam.score is not None else "not scored"
    parts = [f"{a} {seam.direction} {b}: seam score {score}"]
    parts.append(
        f"{'+'.join(seam.kinds)}, confidence {seam.confidence:.2f}" if seam.kinds else "no structural edge"
    )
    parts.append(f"reach {seam.reach} service(s)")
    if seam.co_change is None:
        parts.append("co-change not measured")
    elif seam.declared and seam.co_change:
        parts.append(f"co-change {seam.co_change:.2f}, behind a contract")
    elif seam.co_change:
        parts.append(f"co-change {seam.co_change:.2f} with no contract")
    else:
        parts.append("no co-change")
    return ", ".join(parts)


async def collect(ctx: WorkspaceReportContext) -> list[dict[str, Any]]:
    from repowise.core.workspace.seams import rank_seams

    graph = ctx.system_graph()
    if graph is None:
        return missing_graph("seams", _METHOD)
    evidence = ctx.evidence()
    if evidence.state == "insufficient":
        return [
            check(
                "seams",
                reason=f"not determined: {evidence.summary}",
                method=_METHOD,
                basis="unresolved",
            )
        ]
    history = await git_history(ctx)
    commits = {a: f["commits"] for a, f in history.items() if not isinstance(f, Exception)}
    seams = rank_seams(graph, commits)
    unmeasured = sorted({note.split(": ", 1)[1] for s in seams for note in s.missing})
    details = capped([_line(s) for s in seams], limit=_LISTED)
    if unmeasured:
        details.append(f"co-change not measured: {capped_names(unmeasured)}")
    if failed := unread(history):
        details.append(f"index could not be read, history unknown: {', '.join(failed)}")
    measured = [s for s in seams if s.co_change is not None]
    hidden = sum(1 for s in measured if not s.declared and s.co_change)
    co_change = (
        f"{hidden} of the {len(measured)} with co-change measured change together with no "
        "contract between them"
        if measured
        else "co-change measured for none of them"
    )
    return [
        check(
            "seams",
            reason=(
                f"{len(seams)} service pair(s) joined by an edge, {co_change}; ranked by a "
                "fixed heuristic, highest first" + evidence_note(evidence)
                if seams
                else "no edge joins two services, so there is no seam to rank"
                + evidence_note(evidence)
            ),
            details=details,
            method=_METHOD,
            basis="inferred",
            data={"seams": [s.to_dict() for s in seams]},
        )
    ]


report_sections.register(
    "estate-seams",
    Section(
        title="Riskiest seams",
        summary="Service pairs ranked by link confidence, reach and co-change no contract explains.",
        collect=collect,
        order=50,
        report="estate",
        scope="workspace",
    ),
)

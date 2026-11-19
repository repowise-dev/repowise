"""Provenance: how many recent commits carry a known source, and which agents
and models the agent-marked ones name.

Accountability only: agents and models are named, not counted, so no share of
agent work and no comparison with people can be read off the report. Unknown
commits are never counted as human. Nothing is shown when the repository
switches attribution off.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from repowise.core.report.model import ReportContext, Section, check, report_sections

_METHOD = (
    "public git markers (bot authors, trailers, footers, git-ai notes, Agent Trace files) "
    "and opted-in capture, over commits in the {days} days before the newest indexed one"
)


def _tally(rows: Any) -> tuple[int, int, set[str], set[str]]:
    """``(commits, known, agents, models)`` from ``(attribution, agent, model, n)`` rows."""
    from repowise.core.attribution import AGENT, UNKNOWN, commit_label

    total = known = 0
    agents: set[str] = set()
    models: set[str] = set()
    for attribution, agent, model, n in rows:
        label = commit_label(attribution, agent)
        total += n
        if label != UNKNOWN:
            known += n
        if label == AGENT:
            agents.add(agent or "unnamed agent")
            if model:
                models.add(model)
    return total, known, agents, models


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    from repowise.core.attribution import attribution_enabled
    from repowise.core.events.snapshot import RECENT_DAYS
    from repowise.core.persistence.models import GitCommit

    method = _METHOD.format(days=RECENT_DAYS)
    if not attribution_enabled(ctx.repo_root):
        return [
            check(
                "provenance",
                reason="attribution is switched off for this repository",
                method=method,
                basis="unresolved",
            )
        ]
    repo = GitCommit.repository_id == ctx.repository_id
    newest = await ctx.session.scalar(select(func.max(GitCommit.committed_at)).where(repo))
    if newest is None:
        return [
            check("provenance", reason="no commits are indexed", method=method, basis="unresolved")
        ]
    rows = await ctx.session.execute(
        select(GitCommit.attribution, GitCommit.agent_name, GitCommit.agent_model_id, func.count())
        .where(repo, GitCommit.committed_at >= newest - timedelta(days=RECENT_DAYS))
        .group_by(GitCommit.attribution, GitCommit.agent_name, GitCommit.agent_model_id)
    )
    total, known, agents, models = _tally(rows.all())
    as_of = newest.date().isoformat()
    return [
        check(
            "provenance:coverage",
            reason=f"{known} of {total} commit(s) up to {as_of} carry a known source",
            details=[f"{total - known} commit(s) carry no marker; their source is unknown"],
            method=method,
            # Coverage is how much is known, not a grade: no markers is unknown.
            basis="measured" if known else "unresolved",
            data={"commits": total, "known": known, "window_days": RECENT_DAYS, "as_of": as_of},
        ),
        check(
            "provenance:agents",
            reason=(
                f"{len(agents)} agent(s) named on agent-marked commits"
                if agents
                else "no commit in the window names an agent"
            ),
            details=sorted(agents),
            method=method,
            basis="declared",
            data={"agents": sorted(agents)},
        ),
        check(
            "provenance:models",
            reason=(
                f"{len(models)} model(s) named by Agent Trace records"
                if models
                else "no Agent Trace record in the window names a model"
            ),
            details=sorted(models),
            method="the model an Agent Trace record names for its commit",
            basis="declared",
            data={"models": sorted(models)},
        ),
    ]


report_sections.register(
    "provenance",
    Section(
        title="Provenance",
        summary="Coverage of known provenance, and which agents and models produced changes.",
        collect=collect,
        order=80,
    ),
)

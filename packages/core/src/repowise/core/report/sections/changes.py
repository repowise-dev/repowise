"""Change evidence for a period: the ``changes`` report.

For every change merged between two dates: whether it carries a review
record, which tests reach it, what the local change policy says, which teams
are accountable for what it touched, which agent or model produced it, and
whether a later commit reverted it or named it as the cause of a fix
(:mod:`repowise.core.report.change_answers`). Then a period summary and a
method appendix saying what git alone can and cannot attest.

One evidence pass feeds all three sections:
:func:`~repowise.core.changes.period.period_evidence`, the reader ``repowise
changes export`` uses too, which builds and keeps any missing checklist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from repowise.core.report.change_answers import ANSWERS, EntryContext, entry
from repowise.core.report.model import ReportContext, Section, capped, check, report_sections

_CACHE_KEY = "change-period"

_METHOD = (
    "Change Records reconstructed from the main line's merge commits and squash refs, "
    "each with its kept evidence checklist"
)


# -- the pass -----------------------------------------------------------------


async def _request(ctx: ReportContext) -> Any:
    """The period the report covers, its start fixed (by default the
    :data:`~repowise.core.changes.period.DEFAULT_WINDOW_DAYS` before the newest
    indexed commit)."""
    from repowise.core.changes.period import PeriodRequest

    return await (ctx.period or PeriodRequest()).resolved(ctx.session, ctx.repository_id)


async def _period(ctx: ReportContext) -> dict[str, Any]:
    """The period's entries, read once for every section of the report."""
    if _CACHE_KEY not in ctx._cache:
        try:
            ctx._cache[_CACHE_KEY] = await _read_period(ctx)
        except Exception as exc:  # each section reports it; none re-runs the pass
            ctx._cache[_CACHE_KEY] = exc
    got = ctx._cache[_CACHE_KEY]
    if isinstance(got, Exception):
        raise got
    return got


async def _read_period(ctx: ReportContext) -> dict[str, Any]:
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.period import period_evidence
    from repowise.core.changes.record import change_record
    from repowise.core.changes.source import resolve_source
    from repowise.core.ownership.directory import team_directories

    policy, policy_error = _load_policy(ctx)
    on = attribution_enabled(ctx.repo_root)
    request = await _request(ctx)
    pairs = []
    async for row, evidence in period_evidence(
        ctx.session, ctx.repository_id, ctx.repo_root, request
    ):
        pairs.append((change_record(row, attribution=on), evidence and evidence.to_dict()))
    wanted = {s for rec, _ in pairs for s in rec["commits"]}
    context = EntryContext(
        policy=policy,
        policy_error=policy_error,
        declared=team_directories.get().load(ctx.repo_root),
        agents=await _agents(ctx, wanted) if on else {},
        attribution=on,
    )
    return {
        "entries": [entry(rec, ev, context) for rec, ev in pairs],
        "policy": policy,
        "policy_error": policy_error,
        "attribution": on,
        "metadata_source": resolve_source()[0],
        "request": request,
        "window": await _window(ctx, request),
    }


def _load_policy(ctx: ReportContext) -> tuple[Any, str | None]:
    from repowise.core.policy import PolicyError, load_policy

    try:
        return load_policy(ctx.repo_root), None
    except PolicyError as exc:
        return None, f"{exc.source} is invalid: {'; '.join(exc.problems)}"


async def _agents(ctx: ReportContext, wanted: set[str]) -> dict[str, tuple[str, str | None]]:
    """``{sha: (agent, model)}`` for the agent-marked commits among *wanted*.

    One query over the repository's commits that carry any agent mark (a small
    set), kept to *wanted* here rather than in an ``IN`` list per batch.
    """
    from sqlalchemy import or_, select

    from repowise.core.attribution import AGENT, commit_label
    from repowise.core.persistence.models import GitCommit

    rows = await ctx.session.execute(
        select(
            GitCommit.sha, GitCommit.attribution, GitCommit.agent_name, GitCommit.agent_model_id
        ).where(
            GitCommit.repository_id == ctx.repository_id,
            or_(GitCommit.attribution == AGENT, GitCommit.agent_name.is_not(None)),
        )
    )
    return {
        sha: (agent or "unnamed agent", model)
        for sha, attribution, agent, model in rows.tuples()
        if sha in wanted and commit_label(attribution, agent) == AGENT
    }


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _oldest_gap(ctx: ReportContext, request: Any, oldest: Any) -> str | None:
    """Why the period starts before the oldest change the index holds, if it does."""
    import asyncio

    from repowise.core.git_refs import resolve

    since = request.since
    oldest_at = _utc(oldest[0])
    if since is not None and since >= oldest_at:
        return None
    if not await asyncio.to_thread(resolve, str(ctx.repo_root), f"{oldest[1]}^"):
        return None  # the oldest change is the first commit: nothing is cut
    return (
        f"the index holds changes merged from {oldest_at.date()} on; earlier changes "
        "in the period are not covered (raise commit_limit and re-index)"
    )


async def _head_gap(ctx: ReportContext, request: Any, newest: datetime | None) -> str | None:
    """Why changes after the indexed commit are missing, if the period reaches them."""
    import asyncio

    from repowise.core.git_refs import resolve

    until = request.until
    if until is not None and newest is not None and until <= _utc(newest):
        return None
    head = await asyncio.to_thread(resolve, str(ctx.repo_root), "HEAD")
    if not head or not ctx.head_commit or head == ctx.head_commit:
        return None
    return (
        f"the index was built at {ctx.head_commit[:12]} and HEAD is {head[:12]}; changes "
        "merged since are not covered until `repowise update` runs"
    )


async def _window(ctx: ReportContext, request: Any) -> dict[str, Any]:
    """Whether the index holds every change the period asks for.

    The index keeps the newest ``commit_limit`` main-line commits, and only up
    to the commit it was last built at.
    """
    from sqlalchemy import func, select

    from repowise.core.persistence.models import Change

    repo = Change.repository_id == ctx.repository_id
    oldest = (
        await ctx.session.execute(
            select(Change.merged_at, Change.head_sha)
            .where(repo, Change.merged_at.is_not(None))
            .order_by(Change.merged_at.asc())
            .limit(1)
        )
    ).first()
    newest = await ctx.session.scalar(select(func.max(Change.merged_at)).where(repo))
    gaps = [
        gap
        for gap in (
            await _oldest_gap(ctx, request, oldest) if oldest is not None else None,
            await _head_gap(ctx, request, newest),
        )
        if gap
    ]
    return {
        "oldest": _utc(oldest[0]).isoformat() if oldest is not None else None,
        "newest": _utc(newest).isoformat() if newest else None,
        "gaps": gaps,
    }


# -- one change -----------------------------------------------------------------


def _change_basis(e: dict[str, Any]) -> str:
    if not e["complete"]:
        return "unresolved"
    return "inferred" if e["tests"].get("basis") == "inferred" else "measured"


def _checklist_line(cl: dict[str, Any]) -> str:
    if not cl["kept"]:
        return "not built (--no-build)"
    return (
        f"{cl['ok']} ok, {cl['attention']} attention, {cl['unknown']} unknown; "
        f"built {cl['generated_at']}"
    )


def _change_lines(e: dict[str, Any]) -> dict[str, str]:
    comps = ", ".join(
        f"{c['name']} ({'team ' + c['team'] if c['team'] else 'no declared team'})"
        for c in e["components"]
    )
    return {
        "components": comps
        or ("outside every component" if e.get("components_known") else "none known"),
        "accountable": e["accountable"]["text"],
        "review": e["review"]["text"],
        "tests": e["tests"]["text"],
        "policy": e["policy"]["text"],
        "provenance": e["provenance"]["text"],
        "outcome": e["outcome"]["text"],
        "checklist": _checklist_line(e["checklist"]),
    }


def _change_check(e: dict[str, Any]) -> dict[str, Any]:
    lines = _change_lines(e)
    merged = (e["merged_at"] or "")[:10] or "unknown"
    return check(
        e["ref"],
        reason=f"{merged} {e['category']}: {e['title']}",
        details=[
            f"{e['files_changed']} file(s), +{e['lines_added']}/-{e['lines_deleted']}",
            *(f"{k}: {v}" for k, v in lines.items()),
        ],
        method=_METHOD,
        basis=_change_basis(e),
        data={
            "change": e,
            "row": {
                "change": e["ref"],
                "merged_at": e["merged_at"],
                "title": e["title"],
                "category": e["category"],
                "teams": ", ".join(e["accountable"].get("teams") or ()),
                **lines,
            },
        },
    )


# -- summary --------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _span(request: Any) -> str:
    since = request.since.date().isoformat() if request.since else "the oldest indexed change"
    until = f"before {request.until.date().isoformat()}" if request.until else "to the newest"
    return f"from {since} {until}"


def _window_words(request: Any) -> str:
    """How the period's start was chosen, in words."""
    if request.window_days:
        anchor = request.since + timedelta(days=request.window_days)
        to = "--until" if anchor == request.until else "the newest indexed commit"
        return (
            f"the {request.window_days} days before {anchor.date().isoformat()} ({to}), the "
            "default without --since; --since DATE sets the start and --since all covers "
            "every indexed change"
        )
    if request.since is not None:
        return "the start --since gave"
    return "every indexed change (--since all)"


def _period_check(period: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    request = period["request"]
    by_category: dict[str, int] = {}
    for e in entries:
        by_category[e["category"]] = by_category.get(e["category"], 0) + 1
    return check(
        "changes-in-period",
        reason=f"{len(entries)} change(s) merged {_span(request)}",
        details=[
            f"period: {_window_words(request)}",
            *(f"{k}: {v}" for k, v in sorted(by_category.items())),
        ],
        method=_METHOD,
        basis="measured",
        data={"changes": len(entries), "by_category": by_category,
              "since": _iso(request.since), "until": _iso(request.until),
              "window_days": request.window_days},
    )


def _window_check(window: dict[str, Any]) -> dict[str, Any]:
    return check(
        "index-window",
        reason="the index covers the whole period" if not window["gaps"]
        else "the index does not cover the whole period",
        details=window["gaps"],
        method="the oldest and newest change the index holds, against the period and HEAD",
        basis="unresolved" if window["gaps"] else "measured",
        data=window,
    )


def _known_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    return {k: sum(e[k]["status"] == "known" for e in entries) for k in (*ANSWERS, "review")}


def _checklists_check(entries: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(entries)
    complete = sum(e["complete"] for e in entries)
    not_kept = sum(not e["checklist"]["kept"] for e in entries)
    known = _known_counts(entries)
    return check(
        "checklists",
        reason=(
            f"{complete} of {n} change(s) answer every question; {n - complete} with at "
            "least one unknown answer, which is missing evidence, not a failure"
        ),
        details=[
            *(f"{k} known: {known[k]} of {n}" for k in ANSWERS),
            *([f"no checklist kept: {not_kept} (built only without --no-build)"] if not_kept else []),
        ],
        method="accountable team, reaching tests, policy and outcome read from each checklist",
        basis="measured",
        data={"complete": complete, "changes": n, "known": known, "not_kept": not_kept},
    )


def _review_check(period: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    n, source = len(entries), period["metadata_source"]
    recorded = _known_counts(entries)["review"]
    return check(
        "review-records",
        reason=(
            f"{recorded} of {n} change(s) carry a review or approval record; "
            f"{n - recorded} not recorded in {source}"
        ),
        method=f"approvals and reviewers from the {source!r} change metadata source",
        basis="declared" if recorded else "unresolved",
        data={"recorded": recorded, "changes": n, "source": source},
    )


def _policy_summary(period: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    method = "the condition rules of .repowise/policies.yaml over each change's checklist"
    if period["policy_error"]:
        return check("policy", reason=period["policy_error"], method=method, basis="unresolved")
    if period["policy"] is None:
        return check(
            "policy",
            reason="no policy configured (.repowise/policies.yaml is absent)",
            method=method,
            basis="declared",
            data={"configured": False},
        )
    counts = {v: 0 for v in ("pass", "warn", "fail", "unknown")}
    for e in entries:
        counts[e["policy"]["verdict"]] += 1
    return check(
        "policy",
        reason=", ".join(f"{k} {v}" for k, v in counts.items()),
        details=capped(
            [f"{e['ref']}: {e['policy']['text']}" for e in entries
             if e["policy"]["verdict"] in ("fail", "warn")]
        ),
        method=method,
        basis="declared",
        data={"configured": True, "source": period["policy"].source, **counts},
    )


def _outcome_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    reverted = [e["ref"] for e in entries if e["outcome"].get("reverted")]
    named = [e["ref"] for e in entries if e["outcome"].get("fix_references")]
    unknown = sum(e["outcome"]["status"] != "known" for e in entries)
    return check(
        "outcomes",
        reason=(
            f"{len(reverted)} reverted, {len(named)} named as the cause by a later fix, "
            f"{unknown} not known"
        ),
        details=capped(
            [f"reverted: {r}" for r in reverted] + [f"named by a later fix: {r}" for r in named]
        ),
        method="reverts that name the change and later commits naming it as the cause, only",
        basis="declared",
        data={"reverted": reverted, "fix_referenced": named, "unknown": unknown},
    )


def _component_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    untouched = outside = 0
    for e in entries:
        untouched += not e["components"]
        outside += not e["components"] and bool(e.get("components_known"))
        for c in e["components"]:
            slot = per.setdefault(c["name"], {"changes": 0, "teams": set()})
            slot["changes"] += 1
            slot["teams"] |= {c["team"]} if c["team"] else set()
    rows = sorted(per.items(), key=lambda kv: (-kv[1]["changes"], kv[0]))
    return check(
        "components",
        reason=(
            f"{len(per)} component(s) changed; {outside} change(s) outside every component, "
            f"{untouched - outside} with components not known"
        ),
        details=capped(
            [
                f"{name}: {v['changes']} change(s); "
                + (f"team(s) {', '.join(sorted(v['teams']))}" if v["teams"] else "no declared team")
                for name, v in rows
            ]
        ),
        method="components each change's files resolve to, with the owning teams the catalog declares",
        basis="declared",
        data={
            "components": {n: {"changes": v["changes"], "teams": sorted(v["teams"])} for n, v in rows},
            "without_component": untouched,
            "outside_every_component": outside,
        },
    )


_PROVENANCE_METHOD = (
    "public git markers (bot authors, trailers, footers, git-ai notes, Agent Trace files) "
    "and opted-in capture; unknown is never counted as human"
)


def _provenance_totals(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Commit coverage and the agents and models named; never a count or share of agent work."""
    prov = [e["provenance"] for e in entries]
    return {
        "commits": sum(p.get("commits") or 0 for p in prov),
        "known": sum(p.get("commits_known") or 0 for p in prov),
        "agents": sorted({a for p in prov for a in p.get("agents") or ()}),
        "models": sorted({m for p in prov for m in p.get("models") or ()}),
    }


def _provenance_summary(period: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not period["attribution"]:
        return check(
            "provenance",
            reason="attribution is switched off for this repository",
            method=_PROVENANCE_METHOD,
            basis="unresolved",
        )
    t = _provenance_totals(entries)
    pct = f" ({round(100 * t['known'] / t['commits'])}%)" if t["commits"] else ""
    named = [("agents", t["agents"]), ("models", t["models"])]
    return check(
        "provenance",
        reason=f"{t['known']} of {t['commits']} commit(s) in the period carry a provenance marker{pct}",
        details=[f"{kind} named: {', '.join(names)}" for kind, names in named if names],
        method=_PROVENANCE_METHOD,
        basis="measured" if t["known"] else "unresolved",
        data=t,
    )


async def _collect_summary(ctx: ReportContext) -> list[dict[str, Any]]:
    period = await _period(ctx)
    entries: list[dict[str, Any]] = period["entries"]
    return [
        _period_check(period, entries),
        _window_check(period["window"]),
        _checklists_check(entries),
        _review_check(period, entries),
        _policy_summary(period, entries),
        _outcome_summary(entries),
        _component_summary(entries),
        _provenance_summary(period, entries),
    ]


async def _collect_changes(ctx: ReportContext) -> list[dict[str, Any]] | None:
    entries = (await _period(ctx))["entries"]
    return [_change_check(e) for e in entries] or None


# -- method appendix --------------------------------------------------------------

_APPENDIX = "what this pack rests on and what it cannot attest"

#: The appendix items whose wording does not depend on the run.
_STATED = {
    "tests": (
        "Tests reaching a change come from an ingested coverage map (measured) or the call "
        "and import graph (inferred), read from the index at the commit each checklist "
        "names; an empty answer counts as no test only against a current coverage map."
    ),
    "accountability": (
        "Owning teams come from the component catalog (spec.owner) and CODEOWNERS team "
        "entries. Individual and unresolved owners are counted, never named; no person is "
        "named, scored or ranked anywhere in this pack."
    ),
    "provenance": (
        "Which agent or model produced a change is read from public git markers and "
        "opted-in capture. A commit with no marker is unknown, never counted as human, and "
        "no share of agent work is reported."
    ),
    "outcomes": (
        "Only explicit links: reverts that name the change and later commits that name it "
        "as the cause in their own words, read at HEAD. Nothing is inferred from blame."
    ),
    "not-attested": (
        "Git alone cannot attest: approvals and who approved, the CI result at merge time, "
        "deployment to production, or an emergency-change ticket. A merged change is not "
        "proof of a production deploy; those records come from the forge, CI and deploy "
        "systems."
    ),
}


def _review_reason(source: str) -> str:
    return (
        "Git records no pull-request approvals or reviewers. Approved-by trailers (GitLab "
        "merge commits) are read when present; any other change reads 'not recorded in "
        f"git', which is unresolved and never a missing review. Source: {source!r}; a forge "
        "metadata source registered on the change-metadata seam supplies approvals."
    )


def _policy_reason(period: dict[str, Any]) -> str:
    policy = period["policy"]
    if policy is None:
        return (
            period["policy_error"]
            or "No .repowise/policies.yaml: no policy is configured, so no change was policy-checked."
        )
    gates = [r.name for r in policy.rules if r.gate]
    return (
        f"The condition rules of {policy.source} are evaluated now against each change's "
        "kept checklist; a rule whose evidence is unknown is not evaluated. This is not the "
        "verdict CI reached at merge time."
        + (f" Gate rules ({', '.join(gates)}) run over the live tree and are not re-run." if gates else "")
    )


def _period_reason(request: Any) -> str:
    return (
        f"The period runs {_span(request)}: {_window_words(request)}. A change counts "
        "in it by the date it was merged (UTC)."
    )


def _snapshot_reason(ctx: ReportContext) -> str:
    head = (ctx.head_commit or "")[:12] or "the indexed commit"
    return (
        "Each checklist is kept from its first build, stamped with when it was built and "
        f"the indexed commit it read ({head} for this report); outcomes are read again "
        "whenever HEAD moves, components and owners whenever the component catalog "
        "changes, and --refresh rebuilds every checklist."
    )


async def _collect_method(ctx: ReportContext) -> list[dict[str, Any]]:
    period = await _period(ctx)
    entries = period["entries"]
    items = [
        ("period", _period_reason(period["request"]), "measured"),
        ("review", _review_reason(period["metadata_source"]),
         "declared" if any(e["review"]["status"] == "known" for e in entries) else "unresolved"),
        ("tests", _STATED["tests"],
         "measured" if any(e["tests"].get("measured") for e in entries) else "inferred"),
        ("policy", _policy_reason(period), "unresolved" if period["policy_error"] else "declared"),
        ("accountability", _STATED["accountability"], "declared"),
        ("provenance", _STATED["provenance"], "measured" if period["attribution"] else "unresolved"),
        ("outcomes", _STATED["outcomes"], "declared"),
        ("snapshot", _snapshot_reason(ctx), "measured"),
        ("not-attested", _STATED["not-attested"], "unresolved"),
    ]
    return [check(name, reason=reason, method=_APPENDIX, basis=basis) for name, reason, basis in items]


report_sections.register(
    "change-summary",
    Section(
        title="Period summary",
        summary="Changes merged in the period and how many of the audit questions each answers",
        collect=_collect_summary,
        order=10,
        report="changes",
    ),
)
report_sections.register(
    "changes",
    Section(
        title="Changes",
        summary=(
            "Each merged change: components and owning teams, review record, reaching tests, "
            "policy result, provenance and outcomes, each with its basis"
        ),
        collect=_collect_changes,
        order=20,
        report="changes",
    ),
)
report_sections.register(
    "change-method",
    Section(
        title="Method and basis",
        summary="What each answer rests on, and what git alone cannot attest",
        collect=_collect_method,
        order=90,
        report="changes",
    ),
)

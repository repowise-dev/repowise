"""Change evidence for a period: the ``changes`` report.

For every change merged between two dates: whether it carries a review
record, which tests reach it, what the local change policy says, which teams
are accountable for what it touched, which agent or model produced it, and
whether a later commit reverted it or named it as the cause of a fix. Then a
period summary and a method appendix saying what git alone can and cannot
attest.

One evidence pass feeds all three sections:
:func:`~repowise.core.changes.period.period_evidence`, the reader ``repowise
changes export`` uses too, which builds and keeps any missing checklist. The
checklist is read as data: an unknown item stays unknown, never a negative.
People are never named: owners are teams, reviewers are counted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from repowise.core.report.model import ReportContext, Section, capped, check, report_sections

_CACHE_KEY = "change-period"

#: The questions completeness is judged on; review is reported on its own,
#: since git alone never records it.
_ANSWERS = ("accountable", "tests", "policy", "outcome")

#: Why every answer reads unknown for a change whose checklist was not built.
NOT_KEPT = "no checklist kept; run without --no-build to build it"

_METHOD = (
    "Change Records reconstructed from the main line's merge commits and squash refs, "
    "each with its kept evidence checklist"
)


# -- the pass -----------------------------------------------------------------


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
    declared = team_directories.get().load(ctx.repo_root)
    on = attribution_enabled(ctx.repo_root)
    pairs = []
    async for row, evidence in period_evidence(
        ctx.session,
        ctx.repository_id,
        ctx.repo_root,
        since=ctx.since,
        until=ctx.until,
        refresh=ctx.refresh,
        build=ctx.build,
        progress=ctx.progress,
        plan=ctx.plan,
    ):
        pairs.append((change_record(row, attribution=on), evidence and evidence.to_dict()))
    agents = await _agents(ctx, [s for rec, _ in pairs for s in rec["commits"]]) if on else {}
    entries = [
        entry(
            rec,
            ev,
            policy=policy,
            policy_error=policy_error,
            declared=declared,
            agents=agents,
            attribution=on,
        )
        for rec, ev in pairs
    ]
    return {
        "entries": entries,
        "policy": policy,
        "policy_error": policy_error,
        "attribution": on,
        "metadata_source": resolve_source()[0],
        "window": await _window(ctx),
    }


def _load_policy(ctx: ReportContext) -> tuple[Any, str | None]:
    from repowise.core.policy import PolicyError, load_policy

    try:
        return load_policy(ctx.repo_root), None
    except PolicyError as exc:
        return None, f"{exc.source} is invalid: {'; '.join(exc.problems)}"


async def _agents(ctx: ReportContext, shas: list[str]) -> dict[str, tuple[str, str | None]]:
    """``{sha: (agent, model)}`` for the agent-marked commits among *shas*."""
    from sqlalchemy import select

    from repowise.core.attribution import AGENT, commit_label
    from repowise.core.persistence.models import GitCommit

    out: dict[str, tuple[str, str | None]] = {}
    wanted = list(dict.fromkeys(shas))
    for i in range(0, len(wanted), 500):
        rows = await ctx.session.execute(
            select(
                GitCommit.sha, GitCommit.attribution, GitCommit.agent_name, GitCommit.agent_model_id
            ).where(
                GitCommit.repository_id == ctx.repository_id,
                GitCommit.sha.in_(wanted[i : i + 500]),
            )
        )
        for sha, attribution, agent, model in rows.tuples():
            if commit_label(attribution, agent) == AGENT:
                out[sha] = (agent or "unnamed agent", model)
    return out


async def _window(ctx: ReportContext) -> dict[str, Any]:
    """Whether the index holds every change the period asks for.

    The index keeps the newest ``commit_limit`` main-line commits, and only up
    to the commit it was last built at.
    """
    import asyncio

    from sqlalchemy import func, select

    from repowise.core.git_refs import resolve
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
    gaps: list[str] = []
    root = str(ctx.repo_root)
    if oldest is not None:
        oldest_at = _utc(oldest[0])
        cut = await asyncio.to_thread(resolve, root, f"{oldest[1]}^")
        if cut and (ctx.since is None or ctx.since < oldest_at):
            gaps.append(
                f"the index holds changes merged from {oldest_at.date()} on; earlier changes "
                "in the period are not covered (raise commit_limit and re-index)"
            )
    head = await asyncio.to_thread(resolve, root, "HEAD")
    reaches_head = ctx.until is None or newest is None or ctx.until > _utc(newest)
    if head and ctx.head_commit and head != ctx.head_commit and reaches_head:
        gaps.append(
            f"the index was built at {ctx.head_commit[:12]} and HEAD is {head[:12]}; changes "
            "merged since are not covered until `repowise update` runs"
        )
    return {
        "oldest": _utc(oldest[0]).isoformat() if oldest is not None else None,
        "newest": _utc(newest).isoformat() if newest else None,
        "gaps": gaps,
    }


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# -- one change ---------------------------------------------------------------


def _known(item: dict[str, Any] | None) -> bool:
    return item is not None and item.get("status") != "unknown"


def _reason(item: dict[str, Any] | None, fallback: str = "not known") -> str:
    if item is None:
        return "not in the checklist"
    said = [d["reason"] for d in item.get("details") or () if isinstance(d, dict) and d.get("reason")]
    return said[0] if said else fallback


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "basis": "unresolved", "text": f"unknown ({reason})"}


def _accountable(items: dict[str, Any], declared: Any) -> dict[str, Any]:
    """Owning teams of what the change touched; individual owners are counted, never named."""
    from repowise.core.ownership.directory import OwnerRef

    comps, owners = items.get("components_touched"), items.get("owners")
    teams: set[str] = set()
    individuals: set[str] = set()
    unowned = 0
    if _known(comps):
        teams |= {r["owner"] for r in comps.get("details") or () if r.get("owner")}
    if _known(owners):
        for row in owners.get("details") or ():
            name = row.get("owner")
            if name is None:
                unowned = len(row.get("paths") or ())
                continue
            # A component owner is a team name; a CODEOWNERS entry may be a person.
            ref = declared.from_codeowners(name) if "@" in name else OwnerRef("team", name)
            (teams if ref.kind == "team" else individuals).add(ref.name)
    if not teams and not individuals and not _known(owners):
        return _unknown(_reason(owners))
    parts = [f"team(s) {', '.join(sorted(teams))}" if teams else "no declared team"]
    if individuals:
        parts.append(f"{len(individuals)} individual or unresolved owner(s), not named")
    if unowned:
        parts.append(f"{unowned} path(s) with no declared owner")
    return {
        "status": "known",
        "basis": "declared",
        "teams": sorted(teams),
        "individual_owners": len(individuals),
        "unowned_paths": unowned,
        "text": "; ".join(parts),
    }


def _review(meta: dict[str, Any]) -> dict[str, Any]:
    source = meta.get("source") or "git"
    approvals, reviewers = meta.get("approvals"), meta.get("reviewers")
    if approvals is None and reviewers is None:
        # Git records no approvals: unknown, never a missing review.
        return {"status": "unknown", "basis": "unresolved", "source": source,
                "text": f"not recorded in {source}"}
    said = []
    if approvals is not None:
        said.append(f"{approvals} approval(s)")
    if reviewers is not None:
        said.append(f"{len(reviewers)} reviewer(s)")
    where = "Approved-by trailers in git" if source == "git" else source
    return {
        "status": "known",
        "basis": "declared",
        "source": source,
        "approvals": approvals,
        "reviewers": None if reviewers is None else len(reviewers),
        "text": f"{', '.join(said)} ({where})",
    }


def _tests(item: dict[str, Any] | None) -> dict[str, Any]:
    if not _known(item):
        return _unknown(_reason(item, "no coverage map or graph answer for the changed files"))
    value = item.get("value") if isinstance(item.get("value"), dict) else {}
    if not item.get("details"):
        return {"status": "known", "basis": item["basis"], "text": "no changed source file"}
    said = [
        f"{value.get('measured', 0)} measured and {value.get('inferred', 0)} inferred "
        "test(s) reach the change"
    ]
    if value.get("files_without_tests"):
        said.append(f"{value['files_without_tests']} changed file(s) with no reaching test")
    if value.get("files_unknown"):
        said.append(f"{value['files_unknown']} changed file(s) not known")
    return {
        "status": "known",
        "basis": item["basis"],
        "measured": value.get("measured"),
        "inferred": value.get("inferred"),
        "files_without_tests": value.get("files_without_tests"),
        "files_unknown": value.get("files_unknown"),
        "text": "; ".join(said),
    }


def policy_verdict(policy: Any, policy_error: str | None, evidence: dict[str, Any]) -> dict[str, Any]:
    """The change against the condition rules of *policy*: ``pass``, ``warn``,
    ``fail``, ``unknown`` (a rule could not be evaluated) or ``none`` (no policy).

    Gate rules run a CI command over the live tree, so they are not re-run for
    a past change and are listed as such.
    """
    from repowise.core.policy import Policy, evaluate
    from repowise.core.policy.rules import NOT_APPLICABLE, PASSED, TRIGGERED, UNKNOWN

    if policy_error:
        return {"verdict": "unknown", "status": "unknown", "text": f"unknown ({policy_error})",
                "rules": []}
    if policy is None:
        return {"verdict": "none", "status": "known", "text": "no policy configured", "rules": []}
    gates = [r.name for r in policy.rules if r.gate]
    rules = tuple(r for r in policy.rules if not r.gate)
    gate_note = f"; gate rule(s) {', '.join(gates)} not re-run for a past change" if gates else ""
    if not rules:
        return {"verdict": "unknown", "status": "unknown", "rules": [], "gates_not_rerun": gates,
                "text": f"unknown (the policy has only gate rules){gate_note}"}
    results = evaluate(Policy(rules, policy.source), evidence)
    outcomes = [r.outcome for r in results]
    if any(r.fails for r in results):
        verdict = "fail"
    elif TRIGGERED in outcomes:
        verdict = "warn"
    elif UNKNOWN in outcomes:
        verdict = "unknown"
    else:
        verdict = "pass"
    counts = [
        (outcomes.count(TRIGGERED), "fired"),
        (outcomes.count(PASSED), "passed"),
        (outcomes.count(NOT_APPLICABLE), "not applicable"),
        (outcomes.count(UNKNOWN), "not evaluated"),
    ]
    fired = [r.rule.name for r in results if r.outcome == TRIGGERED]
    said = ", ".join(f"{n} {label}" for n, label in counts if n)
    return {
        "verdict": verdict,
        "status": "unknown" if verdict == "unknown" else "known",
        "rules": [r.to_dict() for r in results],
        "gates_not_rerun": gates,
        "text": f"{verdict} ({said}{'; fired: ' + ', '.join(fired) if fired else ''}){gate_note}",
    }


def _provenance(
    item: dict[str, Any] | None,
    commits: list[str],
    agents: dict[str, tuple[str, str | None]],
    attribution: bool,
) -> dict[str, Any]:
    if not attribution:
        return _unknown("attribution is switched off for this repository")
    value = (item or {}).get("value")
    if not isinstance(value, dict) or "label" not in value:
        # Its commits still count against coverage: unknown, never left out.
        return {**_unknown(_reason(item)), "commits": len(commits), "commits_known": 0}
    label = value["label"]
    known = int(value.get("agent_commits") or 0) + int(value.get("human_attested_commits") or 0)
    total = known + int(value.get("unknown_commits") or 0)
    names = sorted({agents[s][0] for s in commits if s in agents})
    models = sorted({m for s in commits if s in agents and (m := agents[s][1])})
    if label == "agent":
        said = f"agent: {', '.join(names) or 'unnamed agent'}"
        if models:
            said += f", model(s) {', '.join(models)}"
    else:
        said = "human-attested" if label == "human_attested" else "unknown"
    return {
        "status": "unknown" if label == "unknown" else "known",
        "basis": "unresolved" if label == "unknown" else "measured",
        "label": label,
        "agents": names,
        "models": models,
        "commits": total,
        "commits_known": known,
        "coverage": value.get("coverage"),
        "text": f"{said}; {known} of {total} commit(s) carry a provenance marker",
    }


def _outcome(item: dict[str, Any] | None) -> dict[str, Any]:
    if not _known(item):
        return _unknown(_reason(item))
    links = [d for d in item.get("details") or () if d.get("kind") in ("revert", "fix_reference")]
    said = []
    for d in links:
        later = d.get("change_id") or (d.get("sha") or "")[:12]
        if d["kind"] == "revert":
            said.append(f"reverted by {later}" + ("" if d.get("in_force") else " (revert undone since)"))
        else:
            said.append(f"named as the cause by a later fix, {later}")
    reverted = any(d["kind"] == "revert" and d.get("in_force") for d in links)
    return {
        "status": "known",
        "basis": item.get("basis"),
        "reverted": reverted,
        "fix_references": sum(d["kind"] == "fix_reference" for d in links),
        "links": [
            {"kind": d["kind"], "sha": d.get("sha"), "change_id": d.get("change_id")} for d in links
        ],
        "text": "; ".join(said) or "no later commit reverts it or names it as a cause",
    }


def entry(
    record: dict[str, Any],
    evidence: dict[str, Any] | None,
    *,
    policy: Any,
    policy_error: str | None,
    declared: Any,
    agents: dict[str, tuple[str, str | None]],
    attribution: bool,
) -> dict[str, Any]:
    """One change's answers, read from its record and checklist. No person is
    named. With no checklist kept (*evidence* ``None``) every answer it would
    give is unknown."""
    kept = evidence is not None
    evidence = evidence or {"items": []}
    items = {i["kind"]: i for i in evidence.get("items") or ()}
    comps = items.get("components_touched")
    components = (
        [{"name": r["name"], "team": r.get("owner")} for r in comps.get("details") or ()]
        if _known(comps)
        else []
    )
    counts = {"ok": 0, "attention": 0, "unknown": 0}
    for item in evidence.get("items") or ():
        counts[item["status"] if item["status"] in ("ok", "attention") else "unknown"] += 1
    answers = {
        "accountable": _accountable(items, declared),
        "review": _review(record["metadata"]),
        "tests": _tests(items.get("reaching_tests")),
        "policy": policy_verdict(policy, policy_error, evidence),
        "provenance": _provenance(items.get("provenance"), record["commits"], agents, attribution),
        "outcome": _outcome(items.get("outcomes")),
    }
    if not kept:
        for k in ("accountable", "tests", "outcome"):
            answers[k] = _unknown(NOT_KEPT)
        answers["provenance"] = {**answers["provenance"], **_unknown(NOT_KEPT)}
    return {
        "id": record["id"],
        "ref": record["forge_ref"] or record["id"],
        "title": record["title"],
        "merged_at": record["merged_at"],
        "category": record["category"],
        "head_sha": record["head_sha"],
        "files_changed": record["files_changed"],
        "lines_added": record["lines_added"],
        "lines_deleted": record["lines_deleted"],
        "components": components,
        **answers,
        "complete": all(answers[k]["status"] == "known" for k in _ANSWERS),
        "checklist": {
            "kept": kept,
            **counts,
            "generated_at": evidence.get("generated_at"),
            "index_commit": evidence.get("index_commit"),
            "items": [
                {"kind": i["kind"], "status": i["status"], "basis": i["basis"]}
                for i in evidence.get("items") or ()
            ],
        },
    }


# -- sections -----------------------------------------------------------------


def _change_check(e: dict[str, Any]) -> dict[str, Any]:
    if not e["complete"]:
        basis = "unresolved"
    else:
        basis = "inferred" if e["tests"].get("basis") == "inferred" else "measured"
    comps = ", ".join(
        f"{c['name']} ({'team ' + c['team'] if c['team'] else 'no declared team'})"
        for c in e["components"]
    )
    cl = e["checklist"]
    checklist = (
        f"{cl['ok']} ok, {cl['attention']} attention, {cl['unknown']} unknown; "
        f"built {cl['generated_at']}"
        if cl["kept"]
        else "not built (--no-build)"
    )
    lines = {
        "components": comps or "none known",
        "accountable": e["accountable"]["text"],
        "review": e["review"]["text"],
        "tests": e["tests"]["text"],
        "policy": e["policy"]["text"],
        "provenance": e["provenance"]["text"],
        "outcome": e["outcome"]["text"],
        "checklist": checklist,
    }
    merged = (e["merged_at"] or "")[:10] or "unknown"
    return check(
        e["ref"],
        reason=f"{merged} {e['category']}: {e['title']}",
        details=[
            f"{e['files_changed']} file(s), +{e['lines_added']}/-{e['lines_deleted']}",
            *(f"{k}: {v}" for k, v in lines.items()),
        ],
        method=_METHOD,
        basis=basis,
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


def _period_text(ctx: ReportContext) -> str:
    since = ctx.since.date().isoformat() if ctx.since else "the oldest indexed change"
    until = f"before {ctx.until.date().isoformat()}" if ctx.until else "to the newest"
    return f"merged from {since} {until}"


async def _collect_summary(ctx: ReportContext) -> list[dict[str, Any]]:
    period = await _period(ctx)
    entries: list[dict[str, Any]] = period["entries"]
    n = len(entries)
    window = period["window"]
    by_category: dict[str, int] = {}
    for e in entries:
        by_category[e["category"]] = by_category.get(e["category"], 0) + 1
    checks = [
        check(
            "changes-in-period",
            reason=f"{n} change(s) {_period_text(ctx)}",
            details=[f"{k}: {v}" for k, v in sorted(by_category.items())],
            method=_METHOD,
            basis="measured",
            data={"changes": n, "by_category": by_category, "since": _iso(ctx.since),
                  "until": _iso(ctx.until)},
        ),
        check(
            "index-window",
            reason="the index covers the whole period" if not window["gaps"]
            else "the index does not cover the whole period",
            details=window["gaps"],
            method="the oldest and newest change the index holds, against the period and HEAD",
            basis="unresolved" if window["gaps"] else "measured",
            data=window,
        ),
    ]
    complete = sum(e["complete"] for e in entries)
    not_kept = sum(not e["checklist"]["kept"] for e in entries)
    known = {k: sum(e[k]["status"] == "known" for e in entries) for k in (*_ANSWERS, "review")}
    checks.append(
        check(
            "checklists",
            reason=(
                f"{complete} of {n} change(s) answer every question; {n - complete} with at "
                "least one unknown answer, which is missing evidence, not a failure"
            ),
            details=[
                *(f"{k} known: {known[k]} of {n}" for k in _ANSWERS),
                *([f"no checklist kept: {not_kept} (built only without --no-build)"] if not_kept else []),
            ],
            method="accountable team, reaching tests, policy and outcome read from each checklist",
            basis="measured",
            data={"complete": complete, "changes": n, "known": known, "not_kept": not_kept},
        )
    )
    recorded = known["review"]
    checks.append(
        check(
            "review-records",
            reason=(
                f"{recorded} of {n} change(s) carry a review or approval record; "
                f"{n - recorded} not recorded in {period['metadata_source']}"
            ),
            method=f"approvals and reviewers from the {period['metadata_source']!r} change metadata source",
            basis="declared" if recorded else "unresolved",
            data={"recorded": recorded, "changes": n, "source": period["metadata_source"]},
        )
    )
    checks.append(_policy_summary(period, entries))
    checks.append(_outcome_summary(entries))
    checks.append(_component_summary(entries))
    checks.append(_provenance_summary(period, entries))
    return checks


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
    untouched = 0
    for e in entries:
        if not e["components"]:
            untouched += 1
        for c in e["components"]:
            slot = per.setdefault(c["name"], {"changes": 0, "teams": set()})
            slot["changes"] += 1
            if c["team"]:
                slot["teams"].add(c["team"])
    rows = sorted(per.items(), key=lambda kv: (-kv[1]["changes"], kv[0]))
    return check(
        "components",
        reason=f"{len(per)} component(s) changed; {untouched} change(s) with no known component",
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
        },
    )


def _provenance_summary(period: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    method = (
        "public git markers (bot authors, trailers, footers, git-ai notes, Agent Trace files) "
        "and opted-in capture; unknown is never counted as human"
    )
    if not period["attribution"]:
        return check(
            "provenance",
            reason="attribution is switched off for this repository",
            method=method,
            basis="unresolved",
        )
    commits = sum(e["provenance"].get("commits") or 0 for e in entries)
    known = sum(e["provenance"].get("commits_known") or 0 for e in entries)
    agents = sorted({a for e in entries for a in e["provenance"].get("agents") or ()})
    models = sorted({m for e in entries for m in e["provenance"].get("models") or ()})
    pct = f" ({round(100 * known / commits)}%)" if commits else ""
    # Coverage and names only: no count or share of agent work is given.
    return check(
        "provenance",
        reason=f"{known} of {commits} commit(s) in the period carry a provenance marker{pct}",
        details=[
            *([f"agents named: {', '.join(agents)}"] if agents else []),
            *([f"models named: {', '.join(models)}"] if models else []),
        ],
        method=method,
        basis="measured" if known else "unresolved",
        data={"commits": commits, "known": known, "agents": agents, "models": models},
    )


async def _collect_changes(ctx: ReportContext) -> list[dict[str, Any]] | None:
    entries = (await _period(ctx))["entries"]
    return [_change_check(e) for e in entries] or None


async def _collect_method(ctx: ReportContext) -> list[dict[str, Any]]:
    period = await _period(ctx)
    entries = period["entries"]
    source = period["metadata_source"]
    policy = period["policy"]
    head = (ctx.head_commit or "")[:12] or "the indexed commit"
    recorded = any(e["review"]["status"] == "known" for e in entries)
    measured = any(e["tests"].get("measured") for e in entries)
    if policy is None:
        policy_reason = (
            period["policy_error"]
            or "No .repowise/policies.yaml: no policy is configured, so no change was policy-checked."
        )
    else:
        gates = [r.name for r in policy.rules if r.gate]
        policy_reason = (
            f"The condition rules of {policy.source} are evaluated now against each change's "
            "kept checklist; a rule whose evidence is unknown is not evaluated. This is not the "
            "verdict CI reached at merge time."
            + (f" Gate rules ({', '.join(gates)}) run over the live tree and are not re-run." if gates else "")
        )
    method = "what this pack rests on and what it cannot attest"
    return [
        check(
            "review",
            reason=(
                "Git records no pull-request approvals or reviewers. Approved-by trailers (GitLab "
                "merge commits) are read when present; any other change reads 'not recorded in "
                f"git', which is unresolved and never a missing review. Source: {source!r}; a forge "
                "metadata source registered on the change-metadata seam supplies approvals."
            ),
            method=method,
            basis="declared" if recorded else "unresolved",
        ),
        check(
            "tests",
            reason=(
                "Tests reaching a change come from an ingested coverage map (measured) or the call "
                "and import graph (inferred), read from the index at the commit each checklist "
                "names; an empty answer counts as no test only against a current coverage map."
            ),
            method=method,
            basis="measured" if measured else "inferred",
        ),
        check("policy", reason=policy_reason, method=method,
              basis="unresolved" if period["policy_error"] else "declared"),
        check(
            "accountability",
            reason=(
                "Owning teams come from the component catalog (spec.owner) and CODEOWNERS team "
                "entries. Individual and unresolved owners are counted, never named; no person is "
                "named, scored or ranked anywhere in this pack."
            ),
            method=method,
            basis="declared",
        ),
        check(
            "provenance",
            reason=(
                "Which agent or model produced a change is read from public git markers and "
                "opted-in capture. A commit with no marker is unknown, never counted as human, and "
                "no share of agent work is reported."
            ),
            method=method,
            basis="measured" if period["attribution"] else "unresolved",
        ),
        check(
            "outcomes",
            reason=(
                "Only explicit links: reverts that name the change and later commits that name it "
                "as the cause in their own words, read at HEAD. Nothing is inferred from blame."
            ),
            method=method,
            basis="declared",
        ),
        check(
            "snapshot",
            reason=(
                "Each checklist is kept from its first build, stamped with when it was built and "
                f"the indexed commit it read ({head} for this report); outcomes are read again "
                "whenever HEAD moves, and --refresh rebuilds every checklist."
            ),
            method=method,
            basis="measured",
        ),
        check(
            "not-attested",
            reason=(
                "Git alone cannot attest: approvals and who approved, the CI result at merge time, "
                "deployment to production, or an emergency-change ticket. A merged change is not "
                "proof of a production deploy; those records come from the forge, CI and deploy "
                "systems."
            ),
            method=method,
            basis="unresolved",
        ),
    ]


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

"""One change's answers for the ``changes`` report, read from its record and checklist.

Whether the change carries a review record, which tests reach it, the local
change policy's verdict, which teams are accountable for what it touched,
which agent or model produced it, and whether a later commit reverted it or
named it as the cause of a fix. The checklist is read as data: an unknown item
stays unknown, never a negative. People are never named: owners are teams,
reviewers are counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The questions completeness is judged on; review is reported on its own,
#: since git alone never records it.
ANSWERS = ("accountable", "tests", "policy", "outcome")

#: Why every answer reads unknown for a change whose checklist was not built.
NOT_KEPT = "no checklist kept; run without --no-build to build it"


@dataclass(frozen=True)
class EntryContext:
    """What every change in a period is read against."""

    #: The parsed ``.repowise/policies.yaml``, or ``None``.
    policy: Any = None
    #: Why the policy file could not be read, when it could not.
    policy_error: str | None = None
    #: The declared ownership (:class:`~repowise.core.ownership.directory.DeclaredOwnership`).
    declared: Any = None
    #: ``{sha: (agent, model)}`` for the agent-marked commits of the period.
    agents: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    attribution: bool = True


def _known(item: dict[str, Any] | None) -> bool:
    return item is not None and item.get("status") != "unknown"


def _reason(item: dict[str, Any] | None, fallback: str = "not known") -> str:
    if item is None:
        return "not in the checklist"
    said = [d["reason"] for d in item.get("details") or () if isinstance(d, dict) and d.get("reason")]
    return said[0] if said else fallback


def unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "basis": "unresolved", "text": f"unknown ({reason})"}


# -- accountability -----------------------------------------------------------


def _component_teams(comps: dict[str, Any] | None) -> set[str]:
    if not _known(comps):
        return set()
    return {r["owner"] for r in comps.get("details") or () if r.get("owner")}


def _owner_refs(owners: dict[str, Any], declared: Any) -> tuple[set[str], set[str], int]:
    """``(teams, individuals, unowned paths)`` from the owners item's rows."""
    from repowise.core.ownership.directory import OwnerRef

    teams: set[str] = set()
    individuals: set[str] = set()
    unowned = 0
    for row in owners.get("details") or ():
        name = row.get("owner")
        if name is None:
            unowned = len(row.get("paths") or ())
            continue
        # A component owner is a team name; a CODEOWNERS entry may be a person.
        ref = declared.from_codeowners(name) if "@" in name else OwnerRef("team", name)
        (teams if ref.kind == "team" else individuals).add(ref.name)
    return teams, individuals, unowned


def _accountable_text(teams: set[str], individuals: set[str], unowned: int) -> str:
    parts = [f"team(s) {', '.join(sorted(teams))}" if teams else "no declared team"]
    if individuals:
        parts.append(f"{len(individuals)} individual or unresolved owner(s), not named")
    if unowned:
        parts.append(f"{unowned} path(s) with no declared owner")
    return "; ".join(parts)


def accountable(items: dict[str, Any], declared: Any) -> dict[str, Any]:
    """Owning teams of what the change touched; individual owners are counted, never named."""
    owners = items.get("owners")
    teams = _component_teams(items.get("components_touched"))
    individuals: set[str] = set()
    unowned = 0
    if _known(owners):
        more, individuals, unowned = _owner_refs(owners, declared)
        teams |= more
    if not teams and not individuals and not _known(owners):
        return unknown(_reason(owners))
    return {
        "status": "known",
        "basis": "declared",
        "teams": sorted(teams),
        "individual_owners": len(individuals),
        "unowned_paths": unowned,
        "text": _accountable_text(teams, individuals, unowned),
    }


# -- review and tests -----------------------------------------------------------


def review(meta: dict[str, Any]) -> dict[str, Any]:
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


def _tests_text(value: dict[str, Any]) -> str:
    said = [
        f"{value.get('measured', 0)} measured and {value.get('inferred', 0)} inferred "
        "test(s) reach the change"
    ]
    if value.get("files_without_tests"):
        said.append(f"{value['files_without_tests']} changed file(s) with no reaching test")
    if value.get("files_unknown"):
        said.append(f"{value['files_unknown']} changed file(s) not known")
    return "; ".join(said)


def tests(item: dict[str, Any] | None) -> dict[str, Any]:
    if not _known(item):
        return unknown(_reason(item, "no coverage map or graph answer for the changed files"))
    value = item.get("value") if isinstance(item.get("value"), dict) else {}
    if not item.get("details"):
        return {"status": "known", "basis": item["basis"], "text": "no changed source file"}
    return {
        "status": "known",
        "basis": item["basis"],
        "measured": value.get("measured"),
        "inferred": value.get("inferred"),
        "files_without_tests": value.get("files_without_tests"),
        "files_unknown": value.get("files_unknown"),
        "text": _tests_text(value),
    }


# -- policy -------------------------------------------------------------------


def _verdict(results: list[Any]) -> str:
    from repowise.core.policy.rules import TRIGGERED, UNKNOWN

    outcomes = [r.outcome for r in results]
    if any(r.fails for r in results):
        return "fail"
    if TRIGGERED in outcomes:
        return "warn"
    return "unknown" if UNKNOWN in outcomes else "pass"


def _verdict_text(verdict: str, results: list[Any], gate_note: str) -> str:
    from repowise.core.policy.rules import NOT_APPLICABLE, PASSED, TRIGGERED, UNKNOWN

    outcomes = [r.outcome for r in results]
    counts = [
        (outcomes.count(TRIGGERED), "fired"),
        (outcomes.count(PASSED), "passed"),
        (outcomes.count(NOT_APPLICABLE), "not applicable"),
        (outcomes.count(UNKNOWN), "not evaluated"),
    ]
    fired = [r.rule.name for r in results if r.outcome == TRIGGERED]
    said = ", ".join(f"{n} {label}" for n, label in counts if n)
    return f"{verdict} ({said}{'; fired: ' + ', '.join(fired) if fired else ''}){gate_note}"


def policy_verdict(policy: Any, policy_error: str | None, evidence: dict[str, Any]) -> dict[str, Any]:
    """The change against the condition rules of *policy*: ``pass``, ``warn``,
    ``fail``, ``unknown`` (a rule could not be evaluated) or ``none`` (no policy).

    Gate rules run a CI command over the live tree, so they are not re-run for
    a past change and are listed as such.
    """
    from repowise.core.policy import Policy, evaluate

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
    verdict = _verdict(results)
    return {
        "verdict": verdict,
        "status": "unknown" if verdict == "unknown" else "known",
        "rules": [r.to_dict() for r in results],
        "gates_not_rerun": gates,
        "text": _verdict_text(verdict, results, gate_note),
    }


# -- provenance and outcomes ------------------------------------------------------


def _named(commits: list[str], agents: dict[str, tuple[str, str | None]]) -> tuple[list[str], list[str]]:
    """``(agents, models)`` the change's agent-marked commits name."""
    marked = [agents[s] for s in commits if s in agents]
    return sorted({a for a, _m in marked}), sorted({m for _a, m in marked if m})


def _provenance_said(label: str, names: list[str], models: list[str]) -> str:
    if label != "agent":
        return "human-attested" if label == "human_attested" else "unknown"
    said = f"agent: {', '.join(names) or 'unnamed agent'}"
    return said + (f", model(s) {', '.join(models)}" if models else "")


def provenance(
    item: dict[str, Any] | None, commits: list[str], context: EntryContext
) -> dict[str, Any]:
    if not context.attribution:
        return unknown("attribution is switched off for this repository")
    value = (item or {}).get("value")
    if not isinstance(value, dict) or "label" not in value:
        # Its commits still count against coverage: unknown, never left out.
        return {**unknown(_reason(item)), "commits": len(commits), "commits_known": 0}
    label = value["label"]
    known = int(value.get("agent_commits") or 0) + int(value.get("human_attested_commits") or 0)
    total = known + int(value.get("unknown_commits") or 0)
    names, models = _named(commits, context.agents)
    return {
        "status": "unknown" if label == "unknown" else "known",
        "basis": "unresolved" if label == "unknown" else "measured",
        "label": label,
        "agents": names,
        "models": models,
        "commits": total,
        "commits_known": known,
        "coverage": value.get("coverage"),
        "text": f"{_provenance_said(label, names, models)}; {known} of {total} commit(s) carry a provenance marker",
    }


def _link_text(link: dict[str, Any]) -> str:
    later = link.get("change_id") or (link.get("sha") or "")[:12]
    if link["kind"] != "revert":
        return f"named as the cause by a later fix, {later}"
    return f"reverted by {later}" + ("" if link.get("in_force") else " (revert undone since)")


def outcome(item: dict[str, Any] | None) -> dict[str, Any]:
    if not _known(item):
        return unknown(_reason(item))
    links = [d for d in item.get("details") or () if d.get("kind") in ("revert", "fix_reference")]
    return {
        "status": "known",
        "basis": item.get("basis"),
        "reverted": any(d["kind"] == "revert" and d.get("in_force") for d in links),
        "fix_references": sum(d["kind"] == "fix_reference" for d in links),
        "links": [
            {"kind": d["kind"], "sha": d.get("sha"), "change_id": d.get("change_id")} for d in links
        ],
        "text": "; ".join(map(_link_text, links)) or "no later commit reverts it or names it as a cause",
    }


# -- the entry ------------------------------------------------------------------


def _components(items: dict[str, Any]) -> list[dict[str, Any]]:
    comps = items.get("components_touched")
    if not _known(comps):
        return []
    return [
        {"name": r["name"], "team": r.get("owner")}
        for r in comps.get("details") or ()
        if not r.get("outside")
    ]


def _checklist(evidence: dict[str, Any], kept: bool) -> dict[str, Any]:
    counts = {"ok": 0, "attention": 0, "unknown": 0}
    for item in evidence.get("items") or ():
        counts[item["status"] if item["status"] in ("ok", "attention") else "unknown"] += 1
    return {
        "kept": kept,
        **counts,
        "generated_at": evidence.get("generated_at"),
        "index_commit": evidence.get("index_commit"),
        "items": [
            {"kind": i["kind"], "status": i["status"], "basis": i["basis"]}
            for i in evidence.get("items") or ()
        ],
    }


def _answers(
    record: dict[str, Any], evidence: dict[str, Any], kept: bool, context: EntryContext
) -> dict[str, dict[str, Any]]:
    items = {i["kind"]: i for i in evidence.get("items") or ()}
    answers = {
        "accountable": accountable(items, context.declared),
        "review": review(record["metadata"]),
        "tests": tests(items.get("reaching_tests")),
        "policy": policy_verdict(context.policy, context.policy_error, evidence),
        "provenance": provenance(items.get("provenance"), record["commits"], context),
        "outcome": outcome(items.get("outcomes")),
    }
    if not kept:
        for k in ("accountable", "tests", "outcome"):
            answers[k] = unknown(NOT_KEPT)
        answers["provenance"] = {**answers["provenance"], **unknown(NOT_KEPT)}
    return answers


def entry(
    record: dict[str, Any], evidence: dict[str, Any] | None, context: EntryContext
) -> dict[str, Any]:
    """One change's answers, read from its record and checklist. No person is
    named. With no checklist kept (*evidence* ``None``) every answer it would
    give is unknown."""
    kept = evidence is not None
    evidence = evidence or {"items": []}
    answers = _answers(record, evidence, kept, context)
    items = {i["kind"]: i for i in evidence.get("items") or ()}
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
        "components": _components(items),
        # Known and empty: every file is outside every component.
        "components_known": _known(items.get("components_touched")),
        **answers,
        "complete": all(answers[k]["status"] == "known" for k in ANSWERS),
        "checklist": _checklist(evidence, kept),
    }


__all__ = ["ANSWERS", "NOT_KEPT", "EntryContext", "entry", "policy_verdict", "review"]

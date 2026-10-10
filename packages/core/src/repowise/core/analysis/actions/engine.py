"""Run every rule, merge overlaps, apply user state, rank per horizon.

Pure: facts and states in, a JSON-ready view out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .context import RepoContext, build_context
from .facts import RepoFacts
from .model import HORIZONS, RULE_RANK, TIER_RANK, Action, RuleOutcome
from .rules import code, hygiene, signal
from .summary import summarize

Rule = Callable[[RepoFacts, RepoContext], RuleOutcome]

RULES: tuple[Rule, ...] = (
    hygiene.live_secret,
    code.fresh_regressions,
    code.fragile_file,
    code.fix_concentration,
    code.fix_first,
    hygiene.stale_decision,
    hygiene.knowledge_loss,
    hygiene.broken_doc_refs,
    hygiene.dead_code_batch,
    signal.coverage_missing,
    signal.coverage_stale,
    signal.decisions_unreviewed,
)

#: Stored per horizon. The page shows fewer; the rest stay one click away and
#: the total is always the full count.
KEEP_PER_HORIZON = 20

#: In the head of each tier, no rule takes more than this many places before
#: every other rule with something to say has had one.
PER_RULE_HEAD = 2


@dataclass(frozen=True, slots=True)
class ActionStateRecord:
    state: str
    fingerprint: str
    until: datetime | None = None


#: One ranked unit: the rule's ordering weight and the action as served.
Ranked = tuple[float, dict[str, Any]]


def _hidden(action: dict[str, Any], record: ActionStateRecord | None, now: datetime) -> bool:
    if record is None:
        return False
    if record.state == "snoozed":
        return record.until is not None and _naive(record.until) > _naive(now)
    # Dismissed and done both hold only while the facts are the ones the person
    # saw. A dismissed "raise coverage" returns when ten more fixes land.
    return record.fingerprint == action["fingerprint"]


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _order(actions: list[Ranked]) -> list[Ranked]:
    ranked = sorted(
        actions,
        key=lambda wa: (TIER_RANK[wa[1]["tier"]], RULE_RANK[wa[1]["rule"]], -wa[0], wa[1]["id"]),
    )
    out: list[Ranked] = []
    for tier in sorted({a["tier"] for _, a in ranked}, key=TIER_RANK.__getitem__):
        in_tier = [wa for wa in ranked if wa[1]["tier"] == tier]
        head: list[Ranked] = []
        rest: list[Ranked] = []
        taken: dict[str, int] = {}
        for wa in in_tier:
            rule = wa[1]["rule"]
            if taken.get(rule, 0) < PER_RULE_HEAD:
                head.append(wa)
                taken[rule] = taken.get(rule, 0) + 1
            else:
                rest.append(wa)
        out.extend(head + rest)
    return out


def find_action(facts: RepoFacts, action_id: str) -> Action | None:
    """One action by id, whatever its rank or the person's answer to it."""
    ctx = build_context(facts)
    return next(
        (a for rule in RULES for a in rule(facts, ctx).actions if a.action_id == action_id),
        None,
    )


def rule_actions(facts: RepoFacts) -> dict[str, Any]:
    """Every rule's outcome and actions, JSON-ready: the part of the view the
    stores decide. :func:`rank_actions` applies the person's answers and ranks
    it; index and update store it so a read skips the facts and the rules."""
    ctx = build_context(facts)
    outcomes = [rule(facts, ctx) for rule in RULES]
    return {
        "anchor": facts.anchor.isoformat() if facts.anchor else None,
        "week_start": ctx.week_start.isoformat() if ctx.week_start else None,
        "context": {
            "production_files": ctx.production_files,
            "active_authors_90d": ctx.active_authors_90d,
            "fix_commits_90d": ctx.fix_commits_90d,
            "busy_threshold": ctx.busy_threshold,
            "coverage": facts.coverage.status,
            "history_too_short": ctx.history_too_short,
        },
        "rules": [o.as_dict() for o in outcomes],
        "actions": [(a.weight, a.as_dict()) for o in outcomes for a in o.actions],
    }


def rank_actions(
    ruled: Mapping[str, Any],
    states: Mapping[str, ActionStateRecord] | None = None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """The view: :func:`rule_actions`' output per horizon, answered and ranked."""
    actions: list[Ranked] = [(weight, action) for weight, action in ruled["actions"]]
    states = states or {}
    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        pool = [wa for wa in actions if horizon in wa[1]["horizons"]]
        # A folder action speaks for the fragile files inside it, in the time
        # frame it appears in; listing both there tells one story twice. The
        # week's regression roll-up names files too, but only as a starting
        # point, so it absorbs nothing.
        absorbed = {p for _, a in pool if a["rule"] == "fix_concentration" for p in a["includes"]}
        pool = [
            wa
            for wa in pool
            if not (wa[1]["rule"] == "fragile_file" and wa[1]["target"]["path"] in absorbed)
        ]
        visible = [wa for wa in pool if not _hidden(wa[1], states.get(wa[1]["id"]), now)]
        ordered = _order(visible)
        by_tier: dict[str, int] = {}
        for _, a in ordered:
            by_tier[a["tier"]] = by_tier.get(a["tier"], 0) + 1
        horizons[horizon] = {
            "actions": [a for _, a in ordered[:KEEP_PER_HORIZON]],
            "total": len(ordered),
            "hidden": len(pool) - len(visible),
            "by_tier": by_tier,
        }

    return {
        "status": "available",
        "anchor": ruled["anchor"],
        "week_start": ruled["week_start"],
        "context": ruled["context"],
        "horizons": horizons,
        # Ranked after the person's answers, so composed here, not stored.
        "summary": summarize(horizons, _anchor(ruled["anchor"])),
        "rules": ruled["rules"],
    }


def _anchor(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


def compose_actions(
    facts: RepoFacts,
    states: Mapping[str, ActionStateRecord] | None = None,
    *,
    now: datetime,
) -> dict[str, Any]:
    return rank_actions(rule_actions(facts), states, now=now)

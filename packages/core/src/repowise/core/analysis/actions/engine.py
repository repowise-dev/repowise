"""Run every rule, merge overlaps, apply user state, rank per horizon.

Pure: facts and states in, a JSON-ready view out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NamedTuple

from .context import RepoContext, build_context
from .facts import RepoFacts
from .model import HORIZONS, RULE_RANK, TIER_RANK, Action, RuleOutcome, shared_priority
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

#: The places every surface shows first: the overview's next actions.
NEXT_ACTIONS_HEAD = 3


@dataclass(frozen=True, slots=True)
class ActionStateRecord:
    state: str
    fingerprint: str
    until: datetime | None = None


class Ranked(NamedTuple):
    """One ranked unit: the rule's ordering weight and the action as served."""

    weight: float
    action: dict[str, Any]


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


def _priority(action: dict[str, Any]) -> float:
    """The action's stored priority; a view stored before actions carried one
    reads :func:`shared_priority` from its value or severity."""
    stored = action.get("priority")
    if stored is not None:
        return stored
    return shared_priority(
        action.get("value"), action["severity"], action["confidence"], action["effort"]
    )


def _order(actions: list[Ranked]) -> list[Ranked]:
    # Priority orders a tier; rule rank and the rule's own weight only break ties.
    ranked = sorted(
        actions,
        key=lambda r: (
            TIER_RANK[r.action["tier"]],
            -_priority(r.action),
            RULE_RANK[r.action["rule"]],
            -r.weight,
            r.action["id"],
        ),
    )
    out: list[Ranked] = []
    for tier in sorted({r.action["tier"] for r in ranked}, key=TIER_RANK.__getitem__):
        in_tier = [r for r in ranked if r.action["tier"] == tier]
        head: list[Ranked] = []
        rest: list[Ranked] = []
        taken: dict[str, int] = {}
        for r in in_tier:
            rule = r.action["rule"]
            if taken.get(rule, 0) < PER_RULE_HEAD:
                head.append(r)
                taken[rule] = taken.get(rule, 0) + 1
            else:
                rest.append(r)
        out.extend(head + rest)
    return _reserve_fix_first(out)


def _reserve_fix_first(ordered: list[Ranked]) -> list[Ranked]:
    """The Fix first lead first in its tier's block, so Do next and Fix first
    lead with the same work. It never moves across tiers: an ``act_now`` row
    stays above a ``plan`` lead. The rule emits only due items, and its
    priorities follow the queue's order, so the lead has the highest."""
    leads = [
        (_priority(r.action), r.weight, -i)
        for i, r in enumerate(ordered)
        if r.action["rule"] == "fix_first"
    ]
    if not leads:
        return ordered
    at = -max(leads)[2]
    tier = ordered[at].action["tier"]
    first = next(i for i, r in enumerate(ordered) if r.action["tier"] == tier)
    out = list(ordered)
    out.insert(first, out.pop(at))
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
    actions = [Ranked(weight, action) for weight, action in ruled["actions"]]
    states = states or {}
    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        pool = [r for r in actions if horizon in r.action["horizons"]]
        # A folder action speaks for the fragile files inside it, in the time
        # frame it appears in; listing both there tells one story twice. The
        # week's regression roll-up names files too, but only as a starting
        # point, so it absorbs nothing.
        absorbed = {
            p for r in pool if r.action["rule"] == "fix_concentration" for p in r.action["includes"]
        }
        pool = [
            r
            for r in pool
            if not (r.action["rule"] == "fragile_file" and r.action["target"]["path"] in absorbed)
        ]
        visible = [r for r in pool if not _hidden(r.action, states.get(r.action["id"]), now)]
        ordered = _order(visible)
        by_tier: dict[str, int] = {}
        for r in ordered:
            by_tier[r.action["tier"]] = by_tier.get(r.action["tier"], 0) + 1
        horizons[horizon] = {
            "actions": [r.action for r in ordered[:KEEP_PER_HORIZON]],
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

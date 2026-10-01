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


def _hidden(action: Action, record: ActionStateRecord | None, now: datetime) -> bool:
    if record is None:
        return False
    if record.state == "snoozed":
        return record.until is not None and _naive(record.until) > _naive(now)
    # Dismissed and done both hold only while the facts are the ones the person
    # saw. A dismissed "raise coverage" returns when ten more fixes land.
    return record.fingerprint == action.fingerprint


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _order(actions: list[Action]) -> list[Action]:
    ranked = sorted(
        actions,
        key=lambda a: (TIER_RANK[a.tier], RULE_RANK[a.rule], -a.weight, a.action_id),
    )
    out: list[Action] = []
    for tier in sorted({a.tier for a in ranked}, key=TIER_RANK.__getitem__):
        in_tier = [a for a in ranked if a.tier == tier]
        head: list[Action] = []
        rest: list[Action] = []
        taken: dict[str, int] = {}
        for a in in_tier:
            if taken.get(a.rule, 0) < PER_RULE_HEAD:
                head.append(a)
                taken[a.rule] = taken.get(a.rule, 0) + 1
            else:
                rest.append(a)
        out.extend(head + rest)
    return out


def compose_actions(
    facts: RepoFacts,
    states: Mapping[str, ActionStateRecord] | None = None,
    *,
    now: datetime,
) -> dict[str, Any]:
    ctx = build_context(facts)
    outcomes = [rule(facts, ctx) for rule in RULES]
    actions = [a for o in outcomes for a in o.actions]

    states = states or {}
    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        pool = [a for a in actions if horizon in a.horizons]
        # A folder action speaks for the fragile files inside it, in the time
        # frame it appears in; listing both there tells one story twice. The
        # week's regression roll-up names files too, but only as a starting
        # point, so it absorbs nothing.
        absorbed = {p for a in pool if a.rule == "fix_concentration" for p in a.includes}
        pool = [a for a in pool if not (a.rule == "fragile_file" and a.target_path in absorbed)]
        visible = [a for a in pool if not _hidden(a, states.get(a.action_id), now)]
        ordered = _order(visible)
        by_tier: dict[str, int] = {}
        for a in ordered:
            by_tier[a.tier] = by_tier.get(a.tier, 0) + 1
        horizons[horizon] = {
            "actions": [a.as_dict() for a in ordered[:KEEP_PER_HORIZON]],
            "total": len(ordered),
            "hidden": len(pool) - len(visible),
            "by_tier": by_tier,
        }

    return {
        "status": "available",
        "anchor": facts.anchor.isoformat() if facts.anchor else None,
        "week_start": ctx.week_start.isoformat() if ctx.week_start else None,
        "context": {
            "production_files": ctx.production_files,
            "active_authors_90d": ctx.active_authors_90d,
            "fix_commits_90d": ctx.fix_commits_90d,
            "busy_threshold": ctx.busy_threshold,
            "coverage": facts.coverage.status,
        },
        "horizons": horizons,
        "rules": [o.as_dict() for o in outcomes],
    }

"""What an action is, and the closed vocabularies it is written in.

Mirrored by ``packages/types/src/actions.ts``; ``test_wire_vocabulary_parity``
fails when the two disagree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, get_args

from repowise.core.analysis.next_call import ActionCommand

#: Bumped when a rule's output changes meaning or shape; stored views built
#: under another version are rebuilt.
ACTIONS_MODEL_VERSION = 1

ActionRule = Literal[
    "live_secret",
    "fresh_regressions",
    "fragile_file",
    "fix_concentration",
    "fix_first",
    "stale_decision",
    "knowledge_loss",
    "broken_doc_refs",
    "dead_code_batch",
    "coverage_missing",
    "coverage_stale",
    "decisions_unreviewed",
]
ACTION_RULES: tuple[str, ...] = get_args(ActionRule)

Horizon = Literal["week", "quarter"]
HORIZONS: tuple[str, ...] = get_args(Horizon)

#: ``act_now`` is something wrong that got worse or is live; ``plan`` is work
#: worth scheduling; ``improve_signal`` makes the other two more accurate.
ActionTier = Literal["act_now", "plan", "improve_signal"]
ACTION_TIERS: tuple[str, ...] = get_args(ActionTier)

#: The heading each tier is listed under, on every surface.
TIER_LABELS: dict[str, str] = {
    "act_now": "Now",
    "plan": "Worth planning",
    "improve_signal": "Improve what Repowise can see",
}

#: Where a fact came from. ``unknown`` is a fact too: "coverage unknown" is
#: printed, never read as zero.
FactBasis = Literal["measured", "inferred", "unknown"]
FACT_BASES: tuple[str, ...] = get_args(FactBasis)

ActionSurface = Literal[
    "file",
    "findings",
    "performance",
    "security",
    "doc_drift",
    "dead_code",
    "decisions",
    "commits",
    "coverage",
]
ACTION_SURFACES: tuple[str, ...] = get_args(ActionSurface)

TargetKind = Literal["file", "symbol", "folder", "document", "decision", "repo"]
TARGET_KINDS: tuple[str, ...] = get_args(TargetKind)

Effort = Literal["S", "M", "L"]

RuleStatus = Literal["evaluated", "not_applicable", "unavailable"]
RULE_STATUSES: tuple[str, ...] = get_args(RuleStatus)

ActionStateValue = Literal["dismissed", "snoozed", "done"]
ACTION_STATES: tuple[str, ...] = get_args(ActionStateValue)

TIER_RANK: dict[str, int] = {"act_now": 0, "plan": 1, "improve_signal": 2}

#: Tie-break between rules inside one tier: a claim that code is wrong or got
#: worse outranks a claim that it is costly, which outranks tidiness.
RULE_RANK: dict[str, int] = {rule: i for i, rule in enumerate(ACTION_RULES)}


@dataclass(frozen=True, slots=True)
class WhyFact:
    label: str
    value: str
    basis: FactBasis = "measured"

    def as_dict(self) -> dict[str, str]:
        return {"label": self.label, "value": self.value, "basis": self.basis}


@dataclass(frozen=True, slots=True)
class ActionDetail:
    """One piece of evidence behind an action: a finding, a site, a reference.

    What an agent needs to start without re-deriving the list: where, what,
    and why. ``ref`` is the commit or stored id that produced it.
    """

    path: str
    line: int | None = None
    symbol: str | None = None
    marker: str | None = None
    severity: str | None = None
    reason: str = ""
    ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "symbol": self.symbol,
            "marker": self.marker,
            "severity": self.severity,
            "reason": self.reason,
            "ref": self.ref,
        }


#: Evidence rows carried per action. The total says how many there were.
DETAILS_CAP = 40


@dataclass(frozen=True, slots=True)
class Action:
    rule: ActionRule
    tier: ActionTier
    horizons: tuple[Horizon, ...]
    severity: str
    #: Verb first. Paths and symbols are wrapped in backticks, which every
    #: renderer sets in mono: "Raise test coverage on `a/b.py` from 62%".
    title: str
    impact: str
    why: tuple[WhyFact, ...]
    target_kind: TargetKind
    target_path: str
    surface: ActionSurface
    effort: Effort
    confidence: Literal["high", "medium"]
    done_when: str
    #: Rule-local ordering weight, comparable only within its rule.
    weight: float = 0.0
    target_symbol: str | None = None
    #: What makes this action the same action next time, when the target alone
    #: does not: two performance opportunities can share a symbol, and a
    #: fragile file's lead function can change without the file's story
    #: changing. Defaults to ``target_path:target_symbol``.
    identity: str | None = None
    evidence_ids: tuple[str, ...] = ()
    evidence_total: int = 0
    #: Files a folder-level action absorbed, so their own rows do not repeat.
    includes: tuple[str, ...] = ()
    command: str | None = None
    #: The biomarker behind the action, when one names it; renderers label it
    #: through their own glossary.
    marker: str | None = None
    #: Changes when the facts behind the action change materially. A dismissal
    #: records it, and a dismissed action returns when it no longer matches.
    fingerprint: str = ""
    details: tuple[ActionDetail, ...] = ()
    details_total: int = 0
    commands: tuple[ActionCommand, ...] = ()

    @property
    def action_id(self) -> str:
        identity = self.identity or f"{self.target_path}:{self.target_symbol or ''}"
        key = f"{self.rule}:{identity}"
        return "act_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "rule": self.rule,
            "tier": self.tier,
            "horizons": list(self.horizons),
            "severity": self.severity,
            "title": self.title,
            "impact": self.impact,
            "why": [w.as_dict() for w in self.why],
            "target": {
                "kind": self.target_kind,
                "path": self.target_path,
                "symbol": self.target_symbol,
            },
            "surface": self.surface,
            "effort": self.effort,
            "confidence": self.confidence,
            "done_when": self.done_when,
            "command": self.command,
            "marker": self.marker,
            "evidence_ids": list(self.evidence_ids),
            "evidence_total": self.evidence_total,
            "includes": list(self.includes),
            "fingerprint": self.fingerprint,
            "details": [d.as_dict() for d in self.details[:DETAILS_CAP]],
            "details_total": max(self.details_total, len(self.details)),
            "commands": [c.as_dict() for c in self.commands],
        }


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    rule: ActionRule
    status: RuleStatus
    reason: str = ""
    actions: tuple[Action, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "status": self.status,
            "reason": self.reason,
            "emitted": len(self.actions),
        }


def fingerprint(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

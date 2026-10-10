"""What one Fix-first item is, and the closed vocabularies it is written in.

Mirrored by ``packages/types/src/fix-first.ts``; ``test_wire_vocabulary_parity``
fails when the two disagree.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, get_args

from repowise.core.analysis.next_call import ActionCommand

#: Bumped when ranking, eligibility or the item shape changes meaning.
FIX_FIRST_MODEL_VERSION = 1

FixTier = Literal["now", "next", "later"]
FIX_TIERS: tuple[str, ...] = get_args(FixTier)

FixKind = Literal["refactor", "perf_fix", "finding"]
FIX_KINDS: tuple[str, ...] = get_args(FixKind)

FixImproves = Literal["defect", "maintainability", "performance"]
FIX_IMPROVES: tuple[str, ...] = get_args(FixImproves)

FixGainKind = Literal["health_points", "performance"]
FIX_GAIN_KINDS: tuple[str, ...] = get_args(FixGainKind)

FixEffort = Literal["S", "M", "L", "XL"]
FIX_EFFORTS: tuple[str, ...] = get_args(FixEffort)

FixLevel = Literal["high", "medium", "low"]
FIX_LEVELS: tuple[str, ...] = get_args(FixLevel)

FixFactBasis = Literal["measured", "inferred", "unknown"]
FIX_FACT_BASES: tuple[str, ...] = get_args(FixFactBasis)

# ``unknown``, ``gated_off``, ``expected`` and ``no_strategy`` are the
# performance default queue's own reasons (``opportunity_rank.DEFAULT_QUEUE_EXCLUSIONS``);
# ``gated_off`` also keeps out any other unit in a function a constant-false
# flag switches off. ``unreachable``: an open dead-code finding covers the target.
FixExclusion = Literal[
    "test",
    "tooling",
    "unknown",
    "generated",
    "expected",
    "no_strategy",
    "no_plan",
    "below_min_worth",
    "history_only",
    "vendored",
    "docs_example",
    "deprecated",
    "gated_off",
    "unreachable",
    "inherent_dispatch",
    "small_function",
    "no_concrete_step",
    "low_value_kind",
]
FIX_EXCLUSIONS: tuple[str, ...] = get_args(FixExclusion)

#: Exclusions that say where the code lives, not whether the work is worth
#: doing: a unit excluded for one of these was never in scope.
SCOPE_EXCLUSIONS = frozenset({"test", "tooling", "generated", "vendored", "docs_example"})

#: Tiers a unit is due in: worth scheduling now, not just worth listing.
DUE_TIERS = frozenset({"now", "next"})

FixScope = Literal["production", "all"]
FIX_SCOPES: tuple[str, ...] = get_args(FixScope)

TIER_RANK = {t: i for i, t in enumerate(FIX_TIERS)}
LEVEL_RANK = {"high": 2, "medium": 1, "low": 0}
EFFORT_RANK = {e: i for i, e in enumerate(FIX_EFFORTS)}

_ID_PREFIX = "fix1_"


def fix_id(kind: str, source_id: str) -> str:
    """Stable while the source id is: the item's kind and what it was built from."""
    return _ID_PREFIX + hashlib.sha256(f"{kind}:{source_id}".encode()).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class FixTarget:
    file_path: str
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True, slots=True)
class FixFact:
    label: str
    value: str
    basis: FixFactBasis = "measured"


@dataclass(frozen=True, slots=True)
class FixRankFact:
    factor: str
    value: str


@dataclass(frozen=True, slots=True)
class FixStep:
    order: int
    text: str
    file_path: str
    line: int | None = None
    mechanical: bool = False


@dataclass(frozen=True, slots=True)
class FixAction:
    summary: str
    steps: tuple[FixStep, ...]
    steps_total: int
    mechanical: bool


@dataclass(frozen=True, slots=True)
class FixGain:
    kind: FixGainKind
    value: float | None
    text: str


@dataclass(frozen=True, slots=True)
class FixEffortEstimate:
    bucket: FixEffort
    basis: str


@dataclass(frozen=True, slots=True)
class FixRisk:
    level: FixLevel
    dependents: int | None
    files_touched: int
    text: str


@dataclass(frozen=True, slots=True)
class FixConfidence:
    level: FixLevel
    reason: str


@dataclass(frozen=True, slots=True)
class FixTest:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class FixVerify:
    tests: tuple[FixTest, ...]
    tests_total: int
    command: str | None
    basis: FixFactBasis


@dataclass(frozen=True, slots=True)
class FixContext:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class FixSource:
    opportunity_id: str | None = None
    plan_ids: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FixItem:
    id: str
    rank: int
    tier: FixTier
    kind: FixKind
    improves: FixImproves
    title: str
    target: FixTarget
    why: str
    facts: tuple[FixFact, ...]
    action: FixAction
    gain: FixGain
    effort: FixEffortEstimate
    risk: FixRisk
    confidence: FixConfidence
    verify: FixVerify
    context: tuple[FixContext, ...]
    source: FixSource
    next_call: ActionCommand
    why_ranked: tuple[FixRankFact, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact(self) -> dict[str, Any]:
        """The projection a list renders; the full item is one lookup away."""
        return {
            "id": self.id,
            "tier": self.tier,
            "kind": self.kind,
            "title": self.title,
            "target": asdict(self.target),
            "why": self.why,
            "gain": self.gain.text,
            "effort": self.effort.bucket,
            "confidence": self.confidence.level,
            "next_call": self.next_call.as_dict(),
        }


def _exclusions() -> dict[str, int]:
    return dict.fromkeys(FIX_EXCLUSIONS, 0)


@dataclass(frozen=True, slots=True)
class FixTotals:
    candidates: int = 0
    eligible: int = 0
    shown: int = 0
    excluded: dict[str, int] = field(default_factory=_exclusions)
    #: Distinct functions with work kept out as ``gated_off``: dormant, not gone.
    dormant: int = 0


@dataclass(frozen=True, slots=True)
class FixFirstQueue:
    items: tuple[FixItem, ...] = ()
    totals: FixTotals = field(default_factory=FixTotals)
    by_improves: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIX_IMPROVES, 0))
    model_version: int = FIX_FIRST_MODEL_VERSION
    basis: dict[str, str | None] = field(
        default_factory=lambda: {"analyzed_commit": None, "health_analyzed_at": None}
    )
    #: Every open refactoring opportunity the builder read, by id: ``None``
    #: when it is eligible, else the exclusion that kept it out. The
    #: refactoring view's default scope reads it; never on the wire.
    refactoring_reasons: dict[str, str | None] = field(default_factory=dict)

    @property
    def lead(self) -> FixItem | None:
        return self.items[0] if self.items else None

    def find(self, item_id: str) -> FixItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def as_dict(self, *, compact: bool = False) -> dict[str, Any]:
        render = (lambda i: i.compact()) if compact else (lambda i: i.as_dict())
        return {
            "items": [render(i) for i in self.items],
            "lead": render(self.lead) if self.lead else None,
            "totals": asdict(self.totals),
            "by_improves": dict(self.by_improves),
            "model_version": self.model_version,
            "basis": dict(self.basis),
        }

    def counts(self, shown: int) -> dict[str, Any]:
        """The five-level count vocabulary, on the full queue (``limit=None``).

        ``inventory`` every unit the builder considered; ``in_scope`` those not
        excluded by :data:`SCOPE_EXCLUSIONS`; ``eligible`` those that became an
        item; ``due`` eligible items in a :data:`DUE_TIERS` tier; ``shown`` what
        the caller emits; ``excluded`` every non-zero exclusion, by reason.
        """
        excluded = {reason: n for reason, n in self.totals.excluded.items() if n}
        out_of_scope = sum(n for reason, n in excluded.items() if reason in SCOPE_EXCLUSIONS)
        return {
            "inventory": self.totals.candidates,
            "in_scope": self.totals.candidates - out_of_scope,
            "eligible": self.totals.eligible,
            "due": sum(1 for item in self.items if item.tier in DUE_TIERS),
            "shown": shown,
            "excluded": excluded,
        }


__all__ = [
    "DUE_TIERS",
    "EFFORT_RANK",
    "FIX_EFFORTS",
    "FIX_EXCLUSIONS",
    "FIX_FACT_BASES",
    "FIX_FIRST_MODEL_VERSION",
    "FIX_GAIN_KINDS",
    "FIX_IMPROVES",
    "FIX_KINDS",
    "FIX_LEVELS",
    "FIX_SCOPES",
    "FIX_TIERS",
    "LEVEL_RANK",
    "SCOPE_EXCLUSIONS",
    "TIER_RANK",
    "FixAction",
    "FixConfidence",
    "FixContext",
    "FixEffortEstimate",
    "FixFact",
    "FixFirstQueue",
    "FixGain",
    "FixItem",
    "FixRankFact",
    "FixRisk",
    "FixSource",
    "FixStep",
    "FixTarget",
    "FixTest",
    "FixTotals",
    "FixVerify",
    "fix_id",
]

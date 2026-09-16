"""What a change review contains, as one versioned, uncapped result.

Every surface that reviews a change asks the same questions -- what moved, how
risky is its shape, what did it newly make worse, whose contracts changed,
which tests cover it, is it really several changes -- and each has been
answering them with its own composition of the same primitives. This module
names the answer once so those compositions can converge on it.

Two rules hold here:

* **Uncapped.** Core returns the full population and says how big it is. Caps,
  display ordering, Markdown, URLs and tool-call syntax belong to a surface.
* **A lane that was not consulted says so.** Every lane carries an
  :data:`~repowise.core.analysis.review_directive.EvidenceState`, so an empty
  population is never confused with an absent one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from ..branch_overlap import BranchOverlap
from ..change_contracts import ContractImpact
from ..change_health.models import ChangeHealthDelta, RevisionId
from ..change_health.sources import FileChange
from ..change_risk import ChangeRiskResult, change_risk_payload
from ..independent_changes import IndependentChanges
from ..review_directive import EvidenceState, ReviewDirective

#: Bumped when a serialized bundle stops being readable by the previous reader.
#: Adding a field does not bump it; removing or renaming one, or changing what
#: an existing field means, does.
CHANGE_REVIEW_CONTRACT_VERSION = 1

#: Every lane a bundle can carry, in the order :meth:`ChangeReviewBundle.as_dict`
#: emits them. A reader may rely on each name here being a key of ``lanes``.
LANES: tuple[str, ...] = (
    "manifest",
    "risk",
    "health",
    "contracts",
    "tests",
    "prior_fixes",
    "independent_changes",
    "branch_overlap",
)


@dataclass(frozen=True, slots=True)
class LaneState:
    """How much of one lane's evidence was computed, and why not more."""

    state: EvidenceState
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "reason": self.reason}


def _collapse(lines: set[int]) -> tuple[tuple[int, int], ...]:
    """Adjacent line numbers as inclusive spans: ``{1,2,3,7}`` -> ``((1,3),(7,7))``."""
    spans: list[tuple[int, int]] = []
    for line in sorted(lines):
        if spans and line == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], line)
        else:
            spans.append((line, line))
    return tuple(spans)


@dataclass(frozen=True, slots=True)
class ChangeManifestEntry:
    """One changed path, as every lane of the review counts it.

    Built from the :class:`~repowise.core.analysis.change_health.sources.FileChange`
    the revision source produced, rather than from a parallel mapping that could
    disagree with it about what changed.
    """

    #: The single path naming this change: head side, or base side for a delete.
    path: str
    base_path: str | None
    head_path: str | None
    #: ``added`` | ``modified`` | ``deleted`` | ``renamed``.
    status: str
    #: ``parsed`` | ``unavailable`` | ``truncated`` | ``binary``. Anything but
    #: ``parsed`` means the line-level lanes worked from an incomplete diff.
    diff_reliability: str
    #: Inclusive ``(start, end)`` spans of the lines this change added, collapsed
    #: from the individual line numbers. Empty for a delete or an unparsed diff.
    added_ranges: tuple[tuple[int, int], ...] = ()

    @property
    def added_line_count(self) -> int:
        return sum(end - start + 1 for start, end in self.added_ranges)

    @classmethod
    def from_file_change(cls, change: FileChange) -> ChangeManifestEntry:
        return cls(
            path=change.path,
            base_path=change.base_path or None,
            head_path=change.head_path,
            status=change.status,
            diff_reliability=change.diff_reliability,
            added_ranges=_collapse(change.added_lines),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "base_path": self.base_path,
            "head_path": self.head_path,
            "status": self.status,
            "diff_reliability": self.diff_reliability,
            "added_ranges": [list(span) for span in self.added_ranges],
            "added_line_count": self.added_line_count,
        }


@dataclass(frozen=True, slots=True)
class ChangeReviewRequest:
    """Which change to review, and which files count as part of it.

    The filters are applied once, to the manifest, so every lane downstream
    counts the same universe rather than each re-deriving it.
    """

    revspec: str | None = None
    #: File suffixes to keep. Empty means every suffix.
    extensions: tuple[str, ...] = ()
    #: Gitwildmatch patterns to drop.
    exclude_patterns: tuple[str, ...] = ()
    #: Recent commits sampled to rank this change's shape. 0 disables ranking.
    baseline: int = 200

    def as_dict(self) -> dict[str, Any]:
        return {
            "revspec": self.revspec,
            "extensions": list(self.extensions),
            "exclude_patterns": list(self.exclude_patterns),
            "baseline": self.baseline,
        }


@dataclass(frozen=True, slots=True)
class ChangeReviewBundle:
    """One change, reviewed by every lane that could be consulted."""

    request: ChangeReviewRequest
    base: RevisionId | None
    head: RevisionId | None
    manifest: tuple[ChangeManifestEntry, ...]
    directive: ReviewDirective
    #: One entry per name in :data:`LANES`, always populated.
    lanes: Mapping[str, LaneState]
    risk: ChangeRiskResult | None = None
    health: ChangeHealthDelta | None = None
    contracts: ContractImpact | None = None
    #: The uncapped population from ``analyze_test_impact``, in that analyzer's
    #: own shape. Core does not restate it in a second vocabulary.
    tests: Mapping[str, Any] | None = None
    independent_changes: IndependentChanges | None = None
    branch_overlap: BranchOverlap | None = None
    contract_version: int = CHANGE_REVIEW_CONTRACT_VERSION

    def lane(self, name: str) -> LaneState:
        """The state of *name*, or ``unsupported`` for a lane this build omits."""
        return self.lanes.get(name, LaneState("unsupported", f"unknown lane {name!r}"))

    @property
    def degraded_lanes(self) -> tuple[str, ...]:
        """Lanes whose evidence is anything less than complete."""
        return tuple(name for name in LANES if self.lane(name).state != "available")

    def as_dict(self) -> dict[str, Any]:
        """Stable serialization. Uncapped, and every lane present as a key."""
        return {
            "contract_version": self.contract_version,
            "request": self.request.as_dict(),
            "base": self.base.as_dict() if self.base else None,
            "head": self.head.as_dict() if self.head else None,
            "lanes": {name: self.lane(name).as_dict() for name in LANES},
            "manifest": [entry.as_dict() for entry in self.manifest],
            "directive": _directive_dict(self.directive),
            "risk": change_risk_payload(self.risk) if self.risk else None,
            "health": _delta_dict(self.health) if self.health else None,
            "contracts": _contracts_dict(self.contracts) if self.contracts else None,
            "tests": dict(self.tests) if self.tests is not None else None,
            "independent_changes": (
                self.independent_changes.to_dict() if self.independent_changes else None
            ),
            "branch_overlap": self.branch_overlap.to_dict() if self.branch_overlap else None,
        }


def _directive_dict(directive: ReviewDirective) -> dict[str, Any]:
    # ``fingerprint`` is a property rather than a field, so asdict() drops it --
    # and it is the whole point of the directive to a caller telling a repeat
    # action from a new one.
    return {
        "status": directive.status,
        "headline": directive.headline,
        "evidence_state": directive.evidence_state,
        "fingerprint": directive.fingerprint,
        "reasons": list(directive.reasons),
        "actions": [
            {**asdict(action), "targets": list(action.targets), "fingerprint": action.fingerprint}
            for action in directive.actions
        ],
    }


def _contracts_dict(impact: ContractImpact) -> dict[str, Any]:
    return {
        "status": impact.status,
        "reason": impact.reason,
        "base_is_snapshot": impact.base_is_snapshot,
        "breaking_total": len(impact.breaking),
        "changes_total": len(impact.changes),
        "changes": [{**asdict(c), "is_breaking": c.is_breaking} for c in impact.changes],
    }


def _delta_dict(delta: ChangeHealthDelta) -> dict[str, Any]:
    return {
        "status": delta.status,
        "explanation": delta.explanation,
        "comparison_basis": delta.comparison_basis,
        "base": delta.base.as_dict() if delta.base else None,
        "head": delta.head.as_dict() if delta.head else None,
        "fingerprint": delta.fingerprint.as_dict() if delta.fingerprint else None,
        "scope": delta.scope.as_dict(),
        "introduced_total": delta.introduced_total,
        "worsened_total": delta.worsened_total,
        "resolved_total": delta.resolved_total,
        "unchanged_total": delta.unchanged_total,
        "uncertain_total": delta.uncertain_total,
        "findings": [asdict(finding) for finding in delta.findings],
        "skipped": dict(delta.skipped),
        "limits": list(delta.limits),
    }

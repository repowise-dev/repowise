"""Compose a change review once, from evidence the caller has already gathered.

The split here is deliberate. Everything that needs a database session, a
network call or a snapshot artifact is *collected* by a surface and handed in
as :class:`ChangeReviewEvidence`; everything that decides what the evidence
means happens here, over one manifest, so two surfaces cannot reach different
conclusions from the same facts.

That keeps this module synchronous and free of `AsyncSession`, MCP context,
GitHub clients and Supabase clients, and it keeps the expensive lanes opt-in:
a caller that gathers nothing still gets a bundle, with every lane honestly
marked ``unsupported`` rather than empty.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..change_contracts import (
    ContractImpact,
    analyze_contract_impact,
    unavailable_contract_impact,
)
from ..change_health import ChangeHealthDeltaService, DeltaRequest, RevisionSource
from ..change_health.models import ChangeHealthDelta, RevisionId
from ..change_health.sources import RevisionPair, filter_changes
from ..change_risk import ChangeRiskResult, score_live_change
from ..independent_changes import IndependentChangeEvidence, partition_independent_changes
from ..review_directive import (
    CoveringTestEvidence,
    EvidenceState,
    ReviewDirective,
    review_directive,
)
from .models import (
    LANES,
    ChangeManifestEntry,
    ChangeReviewBundle,
    ChangeReviewRequest,
    LaneState,
)

_NO_SOURCE = "no evidence was supplied for this lane"


@dataclass(frozen=True, slots=True)
class ContractInputs:
    """The parses and graph :func:`analyze_contract_impact` compares over.

    Collected by the surface, because where they come from differs: a live
    checkout parses both sides, while an indexed consumer reads the last
    snapshot for the base -- which is what ``base_is_snapshot`` records, so the
    result never implies a comparison against the change's true base.
    """

    graph: dict
    base_parsed: Any
    head_parsed: Any
    base_is_snapshot: bool = False


@dataclass(frozen=True, slots=True)
class ChangeReviewEvidence:
    """Everything a lane needs that this module will not go and fetch itself.

    Each field left at ``None`` marks its lane ``unsupported``: nobody asked
    that question, which is a different answer from asking and finding nothing.
    """

    #: A pre-scored change. Omitted, the service scores the change itself when
    #: it has a checkout to score from.
    risk: ChangeRiskResult | None = None
    #: An already-computed comparison. Omitted, the service runs one. A caller
    #: that runs the comparison concurrently with its other work -- which every
    #: real surface does, because it is the expensive half -- hands the result
    #: in here rather than losing that concurrency to a synchronous lane.
    health: ChangeHealthDelta | None = None
    contracts: ContractInputs | None = None
    #: The result of ``analyze_test_impact``, uncapped, in its own shape.
    tests: Mapping[str, Any] | None = None
    independent: IndependentChangeEvidence | None = None
    #: Already-ranked overlap, or ``None`` when no branch scan ran.
    branch_overlap: Any = None
    #: Why ``branch_overlap`` is ``None``, when it is absent rather than empty.
    branch_overlap_reason: str | None = None
    #: Lanes the caller wants skipped outright, by name from ``LANES``.
    skip: frozenset[str] = field(default_factory=frozenset)


class ChangeReviewService:
    """Build one :class:`ChangeReviewBundle` for a change."""

    def __init__(
        self,
        source: RevisionSource,
        *,
        repo_path: str | None = None,
        delta_service: ChangeHealthDeltaService | None = None,
    ) -> None:
        self.source = source
        self.repo_path = repo_path
        self.delta_service = delta_service

    def review(
        self,
        request: ChangeReviewRequest | None = None,
        *,
        evidence: ChangeReviewEvidence | None = None,
    ) -> ChangeReviewBundle:
        """Review the change *request* names, with whatever *evidence* allows."""
        request = request or ChangeReviewRequest()
        evidence = evidence or ChangeReviewEvidence()
        try:
            pair = self.source.resolve(request.revspec)
        except ValueError as exc:
            return _unresolved(request, str(exc))

        changes = filter_changes(
            pair.changes,
            extensions=request.extensions,
            exclude_patterns=request.exclude_patterns,
        )
        manifest = tuple(ChangeManifestEntry.from_file_change(c) for c in changes)
        lanes: dict[str, LaneState] = {
            "manifest": LaneState("available")
            if manifest
            else LaneState("unavailable", "the change touches no file this request counts")
        }

        health = self._health(request, evidence, lanes)
        risk = self._risk(request, evidence, lanes)
        self._prior_fixes(risk, lanes)
        contracts = self._contracts(manifest, evidence, lanes)
        tests = self._tests(evidence, lanes)
        independent = self._independent(evidence, lanes)
        overlap = self._branch_overlap(evidence, lanes)

        directive = (
            review_directive(health, _covering_tests(tests, lanes["tests"].state))
            if health is not None
            else ReviewDirective(
                status="unknown",
                headline=f"Change health was not compared: {lanes['health'].reason}",
                evidence_state=lanes["health"].state,
            )
        )
        return ChangeReviewBundle(
            request=request,
            base=RevisionId(pair.base_ref, pair.base_sha, "commit"),
            head=_head_id(pair),
            manifest=manifest,
            directive=directive,
            lanes=lanes,
            risk=risk,
            health=health,
            contracts=contracts,
            tests=tests,
            independent_changes=independent,
            branch_overlap=overlap,
        )

    # -- lanes --------------------------------------------------------------

    def _health(
        self,
        request: ChangeReviewRequest,
        evidence: ChangeReviewEvidence,
        lanes: dict[str, LaneState],
    ) -> ChangeHealthDelta | None:
        if evidence.health is not None:
            delta = evidence.health
        elif "health" in evidence.skip:
            lanes["health"] = LaneState("unsupported", "the caller skipped this lane")
            return None
        else:
            service = self.delta_service or ChangeHealthDeltaService(
                self.source, repo_path=self.repo_path
            )
            delta = service.compare(
                DeltaRequest(
                    repo_path=self.repo_path or "",
                    revspec=request.revspec,
                    extensions=request.extensions,
                    exclude_patterns=request.exclude_patterns,
                )
            )
        # The delta's own status already says how complete the comparison was;
        # restating it in a second vocabulary is how the two drift apart.
        lanes["health"] = LaneState(
            _DELTA_LANE_STATE.get(delta.status, "degraded"),
            None if delta.status == "available" else delta.explanation,
        )
        return delta

    def _risk(
        self,
        request: ChangeReviewRequest,
        evidence: ChangeReviewEvidence,
        lanes: dict[str, LaneState],
    ) -> ChangeRiskResult | None:
        if evidence.risk is not None:
            lanes["risk"] = LaneState("available")
            return evidence.risk
        if "risk" in evidence.skip:
            lanes["risk"] = LaneState("unsupported", "the caller skipped this lane")
            return None
        if self.repo_path is None:
            lanes["risk"] = LaneState(
                "unsupported", "scoring a change's shape needs a checkout or a pre-scored result"
            )
            return None
        try:
            result = score_live_change(
                self.repo_path,
                request.revspec,
                extensions=request.extensions,
                exclude_patterns=request.exclude_patterns,
                baseline=request.baseline,
            )
        # ``score_live_change`` walks git itself, and its ``_git`` raises
        # CalledProcessError on a bad ref and TimeoutExpired on a wedged repo --
        # neither of which is a ValueError or an OSError. One lane failing must
        # not take the bundle down with it, which is the whole point of building
        # a bundle rather than a response.
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            lanes["risk"] = LaneState("degraded", f"the change could not be scored: {exc}")
            return None
        lanes["risk"] = LaneState(
            "available" if result.percentile is not None else "partial",
            None
            if result.percentile is not None
            else (
                f"{result.baseline_sample_size} comparable commits is too small a cohort "
                "to rank this change against"
            ),
        )
        return result

    def _prior_fixes(self, risk: ChangeRiskResult | None, lanes: dict[str, LaneState]) -> None:
        """The fix history the risk lane already walked, stated as its own lane.

        It answers a different question from the score -- what has broken here
        before, rather than how big this diff is -- and it can fail on its own
        while the score survives, so it cannot share the risk lane's state.
        """
        if risk is None:
            lanes["prior_fixes"] = LaneState("unsupported", _NO_SOURCE)
        elif not risk.fix_history_available:
            lanes["prior_fixes"] = LaneState("unavailable", "the fix-history walk could not run")
        else:
            lanes["prior_fixes"] = LaneState("available")

    def _contracts(
        self,
        manifest: Sequence[ChangeManifestEntry],
        evidence: ChangeReviewEvidence,
        lanes: dict[str, LaneState],
    ) -> ContractImpact:
        inputs = evidence.contracts
        if inputs is None or "contracts" in evidence.skip:
            reason = "no graph or parsed revisions were supplied"
            lanes["contracts"] = LaneState("unsupported", reason)
            return unavailable_contract_impact(reason)
        # The changed set and the added ranges come from the one manifest, so
        # the symbols this lane blames are the symbols every other lane counted.
        impact = analyze_contract_impact(
            graph=inputs.graph,
            base_parsed=inputs.base_parsed,
            head_parsed=inputs.head_parsed,
            changed_set={e.path for e in manifest if e.head_path},
            added_ranges_by_file={
                e.path: list(e.added_ranges) for e in manifest if e.added_ranges
            },
            base_is_snapshot=inputs.base_is_snapshot,
        )
        lanes["contracts"] = LaneState(
            "partial" if inputs.base_is_snapshot else "available",
            "the base side is the last indexed snapshot, not this change's base"
            if inputs.base_is_snapshot
            else None,
        )
        return impact

    def _tests(
        self, evidence: ChangeReviewEvidence, lanes: dict[str, LaneState]
    ) -> Mapping[str, Any] | None:
        if evidence.tests is None or "tests" in evidence.skip:
            lanes["tests"] = LaneState("unsupported", _NO_SOURCE)
            return None
        coverage = evidence.tests.get("coverage") or {}
        measured_map = bool(coverage.get("map_present"))
        lanes["tests"] = LaneState(
            "available" if measured_map else "partial",
            None if measured_map else "no measured coverage map; candidates are graph-inferred",
        )
        return evidence.tests

    def _independent(
        self, evidence: ChangeReviewEvidence, lanes: dict[str, LaneState]
    ) -> Any:
        if evidence.independent is None or "independent_changes" in evidence.skip:
            lanes["independent_changes"] = LaneState("unsupported", _NO_SOURCE)
            return None
        lanes["independent_changes"] = LaneState("available")
        # ``None`` here means one change, not a missing answer: the lane ran.
        return partition_independent_changes(evidence.independent)

    def _branch_overlap(
        self, evidence: ChangeReviewEvidence, lanes: dict[str, LaneState]
    ) -> Any:
        if "branch_overlap" in evidence.skip:
            lanes["branch_overlap"] = LaneState("unsupported", "the caller skipped this lane")
            return None
        if evidence.branch_overlap is None:
            lanes["branch_overlap"] = LaneState(
                "unsupported", evidence.branch_overlap_reason or _NO_SOURCE
            )
            return None
        lanes["branch_overlap"] = LaneState("available")
        return evidence.branch_overlap


# -- helpers ----------------------------------------------------------------

#: A delta status is already an evidence state in another vocabulary. Map it
#: rather than re-deriving one, so a new delta status degrades loudly (to
#: ``degraded``) instead of silently reading as available.
#:
#: ``timeout`` maps to ``degraded`` here while the directive reports the same
#: status as ``unavailable``, and that is deliberate: the directive is saying
#: it has no verdict, whereas the lane is saying the comparison began and was
#: cut short. Two readers, two true statements.
_DELTA_LANE_STATE: dict[str, EvidenceState] = {
    "available": "available",
    "partial": "partial",
    "unavailable": "unavailable",
    "unsupported_range": "unsupported",
    "too_large": "unavailable",
    "timeout": "degraded",
    "analyzer_mismatch": "degraded",
    "rules_mismatch": "degraded",
    "stale_baseline": "degraded",
}


def _head_id(pair: RevisionPair) -> RevisionId:
    return RevisionId(
        pair.head_ref,
        pair.head_sha or "",
        "working_tree" if pair.working_tree else "commit",
    )


def _covering_tests(
    tests: Mapping[str, Any] | None, state: EvidenceState
) -> CoveringTestEvidence:
    """The test lane's contribution to the directive, in the directive's terms."""
    if tests is None:
        return CoveringTestEvidence()
    rows = tests.get("recommendations") or []
    ids = tuple(row["test_id"] for row in rows if row.get("test_id"))
    bases = {row.get("basis") for row in rows}
    basis = "measured" if "measured" in bases else ("inferred" if "inferred" in bases else None)
    # Nothing to run AND no measured map is the one case that means "go and
    # establish coverage"; with a map, an empty list is a real answer.
    if not ids and state == "partial":
        return CoveringTestEvidence(state="unavailable", basis=basis)
    return CoveringTestEvidence(state=state, tests_to_run=ids, basis=basis)


def _unresolved(request: ChangeReviewRequest, reason: str) -> ChangeReviewBundle:
    """A bundle for a change that could not be identified at all."""
    return ChangeReviewBundle(
        request=request,
        base=None,
        head=None,
        manifest=(),
        directive=ReviewDirective(
            status="unknown",
            headline=f"The change could not be resolved: {reason}",
            evidence_state="unavailable",
        ),
        lanes={name: LaneState("unavailable", reason) for name in LANES},
    )


__all__ = [
    "ChangeReviewEvidence",
    "ChangeReviewService",
    "ContractInputs",
]

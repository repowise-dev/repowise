"""The CI verdict over documentation drift findings.

A pure function over finding dicts (the :func:`~.serialize.serialize_finding`
shape), so the CLI, the hosted platform and the PR bot reach the same verdict
from the same findings without sharing anything but this module.

A finding fails the gate when its confidence is at or above ``fail_on`` and its
fingerprint is not in the baseline. The fingerprint leaves out the line number,
so an accepted finding stays accepted when an edit above it shifts its line.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .constants import HIGH_CONFIDENCE_THRESHOLD
from .serialize import fingerprint_of


@dataclass(frozen=True)
class GateResult:
    """What the gate decided, and which findings it decided on."""

    passed: bool
    #: At or above ``fail_on`` and not baselined, each with a ``fingerprint``.
    failing: list[dict] = field(default_factory=list)
    #: At or above ``fail_on`` but accepted by the baseline.
    baselined: list[dict] = field(default_factory=list)
    below_threshold: int = 0
    fail_on: float = HIGH_CONFIDENCE_THRESHOLD

    def to_dict(self) -> dict:
        """A stable JSON shape: the verdict, its counts, the failing findings."""
        return {
            "passed": self.passed,
            "fail_on": round(float(self.fail_on), 2),
            "failing_count": len(self.failing),
            "baselined_count": len(self.baselined),
            "below_threshold_count": self.below_threshold,
            "failing": [dict(f) for f in self.failing],
        }


def evaluate_gate(
    findings: Iterable[Mapping[str, Any]],
    *,
    fail_on: float = HIGH_CONFIDENCE_THRESHOLD,
    baseline: frozenset[str] | None = None,
) -> GateResult:
    """Split *findings* into failing, baselined and below-threshold."""
    accepted = baseline or frozenset()
    failing: list[dict] = []
    baselined: list[dict] = []
    below = 0
    for finding in findings:
        if float(finding["confidence"]) < fail_on:
            below += 1
            continue
        row = dict(finding)
        row["fingerprint"] = fingerprint_of(finding)
        (baselined if row["fingerprint"] in accepted else failing).append(row)
    return GateResult(
        passed=not failing,
        failing=failing,
        baselined=baselined,
        below_threshold=below,
        fail_on=float(fail_on),
    )

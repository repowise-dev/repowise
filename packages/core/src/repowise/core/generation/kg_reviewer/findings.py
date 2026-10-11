"""Finding and report types for the KG invariant reviewer.

Plain data, no behaviour: the reviewer's :mod:`checks` return lists of
:class:`Finding`, and the :mod:`runner` aggregates them into a
:class:`ReviewReport`. Kept separate from the checks so a consumer can depend on
the report shape without importing the check logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """How a violated invariant is handled by the gate.

    ``CRITICAL`` invariants are structural guarantees (partition, sequential
    tour) — a violation means the offending item is dropped/blocked. ``WARNING``
    invariants are quality signals (low-signal summary, duplicate reason) —
    logged and surfaced, never silently dropped.
    """

    CRITICAL = "critical"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One violated invariant against one item of the generated KG."""

    check: str
    severity: Severity
    message: str
    target: str = ""  # node/layer/tour id or path the finding is about
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity.value,
            "message": self.message,
            "target": self.target,
            "detail": self.detail,
        }


@dataclass
class ReviewReport:
    """Aggregated outcome of a reviewer run."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def criticals(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.CRITICAL]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """True when no critical invariant was violated."""
        return not self.criticals

    def counts_by_check(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.check] = counts.get(f.check, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "critical_count": len(self.criticals),
            "warning_count": len(self.warnings),
            "counts_by_check": self.counts_by_check(),
            "findings": [f.as_dict() for f in self.findings],
        }

"""Hidden Coupling — files that change together but don't import each other.

Joins two existing signals: ``co_change_partners_json`` (from the git
indexer) and the file-level import edges in the dependency graph. A
high correlation between commits of files A and B that have no static
dependency between them captures behavioral coupling invisible to a
pure type/import analyzer — shared protocols, parallel config, hidden
test fixtures, copy-pasted constants.

Fires when:

- ``commit_count_total`` for both files is at or above the noise floor
- ``co_change_count(A, B) / min(total_A, total_B) >= 0.5``
- there is **no** ``imports`` edge in either direction
- the pair is not a test ↔ production pairing (those are expected to
  co-change)

Tier-aware: when ``co_change_partners_json`` is empty (ESSENTIAL git
tier) the detector short-circuits to zero findings. The empty
short-circuit is explicit so backfill behavior is testable.
"""

from __future__ import annotations

from ....co_change import parse_partners
from ....test_paths import is_test_to_production_pair
from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_COMMITS = 5
_MIN_CORRELATION = 0.5
_HIGH_THRESHOLD = 0.65
_CRITICAL_THRESHOLD = 0.8
_MAX_FINDINGS_PER_FILE = 3
# Absolute number of shared commits required before HIGH/CRITICAL severity is
# allowed. A high correlation ratio over a handful of commits (e.g. 4 shared of
# 5 = 80%) is very likely coincidental — small-sample correlations oversell
# confidence, and a repo-wide scan compares thousands of pairs, so some clear
# any ratio by chance. Until the raw co-change count clears this floor (well
# above ``_MIN_COMMITS``), severity is capped at MEDIUM: a hint, not a verdict.
_MIN_CO_CHANGE_FOR_HIGH = 8


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _severity_for(correlation: float, co_count: float) -> Severity:
    # Confidence-weight by absolute sample size: below the co-change floor the
    # ratio isn't trustworthy enough to assert HIGH/CRITICAL, so cap at MEDIUM.
    if co_count < _MIN_CO_CHANGE_FOR_HIGH:
        return Severity.MEDIUM
    if correlation >= _CRITICAL_THRESHOLD:
        return Severity.CRITICAL
    if correlation >= _HIGH_THRESHOLD:
        return Severity.HIGH
    return Severity.MEDIUM


class HiddenCouplingDetector:
    name = "hidden_coupling"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta = ctx.git_meta or {}
        partners = parse_partners(meta.get("co_change_partners_json"))
        # Explicit ESSENTIAL-tier short-circuit.
        if not partners:
            return []

        total_self = _as_int(meta.get("commit_count_total"))
        if total_self < _MIN_COMMITS:
            return []

        graph = ctx.graph_view
        counts = ctx.repo_commit_counts or {}

        candidates: list[tuple[float, str, float]] = []
        for partner in partners:
            partner_path, co_count = partner.file_path, partner.weight
            if partner_path == ctx.file_path:
                continue
            partner_total = counts.get(partner_path, 0)
            if partner_total < _MIN_COMMITS:
                continue
            denom = min(total_self, partner_total)
            if denom <= 0:
                continue
            correlation = co_count / denom
            if correlation < _MIN_CORRELATION:
                continue
            # Test ↔ production pairs are expected to co-change, so they carry
            # no finding.
            if is_test_to_production_pair(
                ctx.file_path, partner_path, code_language=ctx.language
            ):
                continue
            # Skip when an explicit import edge already documents the
            # coupling.
            if graph is not None and (
                graph.has_edge(ctx.file_path, partner_path, "imports")
                or graph.has_edge(partner_path, ctx.file_path, "imports")
            ):
                continue
            candidates.append((correlation, partner_path, co_count))

        if not candidates:
            return []

        candidates.sort(key=lambda t: t[0], reverse=True)
        capped = candidates[:_MAX_FINDINGS_PER_FILE]

        findings: list[BiomarkerResult] = []
        for correlation, partner_path, co_count in capped:
            findings.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=_severity_for(correlation, co_count),
                    function_name=None,
                    line_start=None,
                    line_end=None,
                    details={
                        "partner": partner_path,
                        "correlation": round(correlation, 3),
                        "co_change_count": round(co_count, 2),
                        "self_commits": total_self,
                        "partner_commits": counts.get(partner_path, 0),
                    },
                    reason=(
                        f"{partner_path} co-changes with this file "
                        f"{round(co_count, 2)} times ({correlation:.0%} of shared "
                        "commits) but no static dependency exists"
                    ),
                )
            )
        return findings


BIOMARKER = HiddenCouplingDetector()

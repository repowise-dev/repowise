"""Hidden Coupling — files that change together but don't import each other.

Reads the co-change records the git indexer persists, each already carrying
what the dependency graph says about the pair. A high correlation between
commits of files A and B that no dependency explains captures behavioral
coupling invisible to a pure type/import analyzer — shared protocols,
parallel config, hidden test fixtures, copy-pasted constants.

Fires when:

- both files clear the commit noise floor
- ``shared_commits(A, B) / min(commits_A, commits_B) >= 0.5``
- the graph does not explain the pair, and could have: a file the parser
  never ingested (a lockfile, a changelog) carries no edge either way, so
  its absence is not evidence
- the pair is not a test ↔ production pairing (those are expected to
  co-change)

Counts come from the co-change walk itself. ``commit_count_total`` looks like
the denominator but is not one: it is collected over a shorter window and
only for files with a code extension, so dividing by it mixes two
populations and understates every ratio it does not simply zero.

An index written before the walk recorded those counts carries neither them
nor the structural label, and yields no findings until it is rebuilt. Silence
is the honest answer there: without the label the detector cannot tell a pair
the graph explains from one it does not.

Tier-aware: when ``co_change_partners_json`` is empty (ESSENTIAL git
tier) the detector short-circuits to zero findings. The empty
short-circuit is explicit so backfill behavior is testable.
"""

from __future__ import annotations

from ....co_change import STRUCTURAL_UNEXPLAINED, parse_partners
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


def _severity_for(correlation: float, co_count: int) -> Severity:
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

        candidates: list[tuple[float, str, int, int, int]] = []
        for partner in partners:
            partner_path = partner.file_path
            if partner_path == ctx.file_path:
                continue
            # Only a pair the graph could have explained and did not.
            if partner.structural != STRUCTURAL_UNEXPLAINED:
                continue
            total_self = partner.self_commits
            partner_total = partner.partner_commits
            if total_self < _MIN_COMMITS or partner_total < _MIN_COMMITS:
                continue
            denom = min(total_self, partner_total)
            if denom <= 0:
                continue
            correlation = partner.support / denom
            if correlation < _MIN_CORRELATION:
                continue
            # Test ↔ production pairs are expected to co-change, so they carry
            # no finding.
            if is_test_to_production_pair(
                ctx.file_path, partner_path, code_language=ctx.language
            ):
                continue
            candidates.append(
                (correlation, partner_path, partner.support, total_self, partner_total)
            )

        if not candidates:
            return []

        candidates.sort(key=lambda t: t[0], reverse=True)
        capped = candidates[:_MAX_FINDINGS_PER_FILE]

        findings: list[BiomarkerResult] = []
        for correlation, partner_path, support, total_self, partner_total in capped:
            findings.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=_severity_for(correlation, support),
                    function_name=None,
                    line_start=None,
                    line_end=None,
                    details={
                        "partner": partner_path,
                        "correlation": round(correlation, 3),
                        "co_change_count": support,
                        "self_commits": total_self,
                        "partner_commits": partner_total,
                    },
                    reason=(
                        f"{partner_path} changed with this file in {support} of its "
                        f"{total_self} commits ({correlation:.0%}) but no static "
                        "dependency exists"
                    ),
                )
            )
        return findings


BIOMARKER = HiddenCouplingDetector()

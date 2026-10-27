"""Ownership Risk — fragmented, drive-by ownership of a file.

Bird et al. ("Don't Touch My Code", FSE 2011) found that the count of
*minor contributors* — developers who each own less than 5% of a file's
commits — is the single strongest defect correlate in the literature
(0.86-0.93), beating size, churn, and complexity. A file with many
drive-by authors and no clear owner accumulates inconsistent mental
models and is far more defect-prone.

This measures *long-run* ownership dispersion, complementing
``developer_congestion`` (which captures *active*, 90-day contention).
The two deliberately overlap; the benchmark measures their independent
lift.

Fires when the file is non-trivial and ownership is fragmented:

- ``total_commits`` ≥ 5, AND
- ``minor_contributors`` ≥ 3 OR ``top_owner_share`` < 0.4.

Under 20 commits no author can hold under 5%, so only the share arm can
fire there. It stays: on the 21-repo T0 defect corpus, dropping it or
requiring 10 to 30 commits for it lowered the marker's size-controlled
coefficient from 0.35 to 0.23-0.31. The reason names the arm that fired.

A file under ``_SMALL_NLOC`` code lines (an interface, a DTO) is capped at
LOW: on the same corpus that raised the score's defect AUC (+0.002 keyword,
+0.004 SZZ labels) and kept every finding.

On a small team (≤ ``SMALL_TEAM_MAX_CONTRIBUTORS`` active contributors
in 90d) ownership dispersion across the same few people is the normal
operating model, not Bird-style drive-by fragmentation — severity is
capped at LOW unless the file is also a hotspot (issue #361).

Reads ``git_meta["top_authors_json"]`` only; no AST work needed. When
git indexing was skipped the field is empty and the detector emits
nothing.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import Severity
from .base import SMALL_TEAM_MAX_CONTRIBUTORS, BiomarkerResult, FileContext

_MIN_COMMITS = 5
#: Below this NLOC the finding stays LOW. Gains were flat from 40 to 100;
#: this is the smallest floor on the plateau. Dropping small files instead
#: (floors 20 to 50) lowered the coefficient and gained less AUC.
_SMALL_NLOC = 40
_MINOR_SHARE = 0.05
_MINOR_THRESHOLD = 3
_TOP_OWNER_FLOOR = 0.4


def _parse_authors(meta: dict[str, Any]) -> list[tuple[str, int]]:
    raw = meta.get("top_authors_json")
    if not raw:
        return []
    try:
        authors = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: list[tuple[str, int]] = []
    for a in authors:
        if not isinstance(a, dict):
            continue
        name = a.get("name") or ""
        try:
            count = int(a.get("commit_count") or 0)
        except (TypeError, ValueError):
            continue
        if count > 0:
            out.append((str(name), count))
    return out


class OwnershipRiskDetector:
    name = "ownership_risk"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta = ctx.git_meta or {}
        authors = _parse_authors(meta)
        total = sum(c for _, c in authors)
        if total < _MIN_COMMITS:
            return []

        minor_contributors = sum(1 for _, c in authors if c / total < _MINOR_SHARE)
        top_owner_share = max((c / total for _, c in authors), default=0.0)

        if not (minor_contributors >= _MINOR_THRESHOLD or top_owner_share < _TOP_OWNER_FLOOR):
            return []

        is_hotspot = bool(meta.get("is_hotspot"))
        if minor_contributors >= 6 and is_hotspot:
            severity = Severity.CRITICAL
        elif minor_contributors >= 5 or (minor_contributors >= 3 and is_hotspot):
            severity = Severity.HIGH
        elif minor_contributors >= 3:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW
        small_file = ctx.nloc < _SMALL_NLOC and severity != Severity.LOW
        if small_file:
            severity = Severity.LOW

        if minor_contributors >= _MINOR_THRESHOLD:
            reason = (
                f"{minor_contributors} minor contributors (each <5% of commits); "
                f"top owner holds {top_owner_share:.0%}"
            )
        else:
            reason = (
                f"no clear owner: the top author holds {top_owner_share:.0%} "
                f"of {total} commits across {len(authors)} contributors"
            )
        if small_file:
            reason += f" (low: only {ctx.nloc} code lines)"

        # Small-team calibration (issue #361): with ≤3 active contributors,
        # ownership concentration/dispersion is the expected shape of the
        # project. Keep the finding (it's accurate) but cap it at LOW unless
        # corroborated by hotspot-grade activity — accurate ≠ actionable.
        active = ctx.repo_active_contributors_90d
        small_team = active is not None and active <= SMALL_TEAM_MAX_CONTRIBUTORS
        if small_team and not is_hotspot and severity != Severity.LOW:
            severity = Severity.LOW
            reason += f" (informational: small team, {active} active contributors in 90d)"

        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "minor_contributors": minor_contributors,
                    "top_owner_share": round(top_owner_share, 3),
                    "contributor_count": len(authors),
                    "total_commits": total,
                    "small_team": small_team,
                },
                reason=reason,
            )
        ]


BIOMARKER = OwnershipRiskDetector()

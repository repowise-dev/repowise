"""Knowledge Loss — the primary owner is gone or barely present.

Bus-factor risk: the person who wrote most of this file no longer
contributes. When the primary owner of a hotspot is also the only deep
contributor (bus_factor 1) and we can't find them in the recent commit
window, the team has effectively lost the institutional knowledge for
this code.

Fires when:

- the file is still changing (``commit_count_90d`` ≥ 1 or it is a
  hotspot; an ``is_stable`` file never fires), AND
- ``bus_factor`` ≤ 1 (the file has one true author), AND
- ``primary_owner_name`` differs from ``recent_owner_name`` OR the
  recent owner contributes < 20% of recent commits

The activity gate is what makes this signal point the right way: an
abandoned-but-stable file is *low* risk (the survivor effect — code
nobody touches doesn't break), so knowledge loss only matters while the
code is live and the lost author's intent is still being edited around.

Severity grades on whether the file is also a hotspot.

On a small team (≤ ``SMALL_TEAM_MAX_CONTRIBUTORS`` active contributors
in 90d), bus-factor-1 files are the expected operating model — the risk
is real but not actionable, so severity is capped at LOW unless the file
is also a hotspot (issue #361).
"""

from __future__ import annotations

from ..models import Severity
from .base import SMALL_TEAM_MAX_CONTRIBUTORS, BiomarkerResult, FileContext

_BUS_FACTOR_THRESHOLD = 1
_RECENT_SHARE_THRESHOLD = 0.2


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _is_hotspot(meta: dict) -> bool:
    if meta.get("is_hotspot"):
        return True
    return _as_int(meta.get("commit_count_90d")) >= 8


class KnowledgeLossDetector:
    name = "knowledge_loss"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta = ctx.git_meta or {}

        # Activity gate: an abandoned-but-stable file is low risk
        # (survivor effect). Only fire while the code is still live.
        if meta.get("is_stable"):
            return []
        if _as_int(meta.get("commit_count_90d")) < 1 and not _is_hotspot(meta):
            return []

        bus = _as_int(meta.get("bus_factor"))
        if bus > _BUS_FACTOR_THRESHOLD or bus == 0:
            # bus_factor 0 means git indexing was skipped or the file
            # has no contributor data — don't fire blind.
            return []

        primary = (meta.get("primary_owner_name") or "").strip()
        recent = (meta.get("recent_owner_name") or "").strip()
        if not primary:
            return []

        recent_share = _as_float(meta.get("recent_owner_commit_pct"))
        share = recent_share / 100.0 if recent_share > 1.0 else recent_share

        primary_gone = primary != recent and recent != ""
        recent_quiet = share < _RECENT_SHARE_THRESHOLD
        if not (primary_gone or recent_quiet):
            return []

        hotspot = _is_hotspot(meta)
        if hotspot:
            severity = Severity.HIGH
        elif primary_gone and recent_quiet:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        reason = (
            f"Primary owner {primary} no longer the recent owner"
            if primary_gone
            else f"Primary owner {primary} barely active (recent share {share:.0%})"
        )

        # Small-team calibration (issue #361): on a 1-3 person team,
        # bus_factor ≤ 1 is the norm, not a silo signal. Keep the finding
        # but cap it at LOW unless the file is hotspot-active.
        active = ctx.repo_active_contributors_90d
        small_team = active is not None and active <= SMALL_TEAM_MAX_CONTRIBUTORS
        if small_team and not hotspot and severity != Severity.LOW:
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
                    "bus_factor": bus,
                    "primary_owner": primary,
                    "recent_owner": recent or None,
                    "recent_owner_share": round(share, 3),
                    "is_hotspot": hotspot,
                    "small_team": small_team,
                },
                reason=reason,
            )
        ]


BIOMARKER = KnowledgeLossDetector()

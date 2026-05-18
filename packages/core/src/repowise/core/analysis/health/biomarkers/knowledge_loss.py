"""Knowledge Loss — the primary owner is gone or barely present.

Bus-factor risk: the person who wrote most of this file no longer
contributes. When the primary owner of a hotspot is also the only deep
contributor (bus_factor 1) and we can't find them in the recent commit
window, the team has effectively lost the institutional knowledge for
this code.

Fires when:

- ``bus_factor`` ≤ 1 (the file has one true author)
- AND ``primary_owner_name`` differs from ``recent_owner_name`` OR the
  recent owner contributes < 20% of recent commits

Severity grades on whether the file is also a hotspot.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

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

        if _is_hotspot(meta):
            severity = Severity.HIGH
        elif primary_gone and recent_quiet:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

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
                    "is_hotspot": _is_hotspot(meta),
                },
                reason=(
                    f"Primary owner {primary} no longer the recent owner"
                    if primary_gone
                    else f"Primary owner {primary} barely active (recent share {share:.0%})"
                ),
            )
        ]


BIOMARKER = KnowledgeLossDetector()

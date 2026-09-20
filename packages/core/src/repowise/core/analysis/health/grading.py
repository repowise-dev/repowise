"""Health bands + repo distribution — the "currency" layer over the score.

The 1-10 health score is the single number we surface. On top of it we put ONE
categorical scheme: five absolute bands, **Excellent / Good / Fair / Needs work
/ At risk**. Absolute rather than percentile, so a score means the same thing
behind a firewall as it does against a public corpus. Excellent and Good share
one green and are told apart by the word.

This module is the SINGLE SOURCE OF TRUTH for the cutoffs. The TypeScript
mirror lives in ``packages/types/src/health.ts`` and a parity test on each side
locks the values. We deliberately ship NO letter grade - a letter on top of the
number plus the band would be a third overlapping scale with arbitrary cliffs.

Inputs are duck-typed (mapping ``m["score"]`` or object ``m.score``) so the same
functions serve the server (dict rows), the CLI (``HealthFileMetricData``), and
the hosted backend (``health.json`` dicts) without per-call-site adapters - the
same convention as ``defect_accuracy.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from .rows import field

HealthBand = Literal["excellent", "good", "fair", "needs_work", "at_risk"]

# Absolute band cutoffs. Frozen (scoring is frozen; this is presentation).
# Green starts at 7.0 because a 7 is not a warning; 4.0 is the long-standing
# most-severe cutoff, so the worst classification keeps its meaning.
EXCELLENT_MIN = 8.5
GOOD_MIN = 7.0
FAIR_MIN = 5.5
NEEDS_WORK_MIN = 4.0

# The score a refactoring is measured against. Deliberately not a band edge:
# it is the deficit target behind refactoring leverage, and moving it would
# reorder every recommendation.
TARGET_SCORE = 8.0

BAND_LABEL: dict[HealthBand, str] = {
    "excellent": "Excellent",
    "good": "Good",
    "fair": "Fair",
    "needs_work": "Needs work",
    "at_risk": "At risk",
}

# The range each band covers, for keys and legends that show the boundaries.
BAND_RANGE_LABEL: dict[HealthBand, str] = {
    "excellent": "8.5+",
    "good": "7.0 to 8.5",
    "fair": "5.5 to 7.0",
    "needs_work": "4.0 to 5.5",
    "at_risk": "under 4.0",
}

# Ordered worst-first, matching how the surface lists files.
BAND_ORDER: tuple[HealthBand, ...] = (
    "at_risk",
    "needs_work",
    "fair",
    "good",
    "excellent",
)

# Terminal colours for the CLI. Fair and Needs work are distinct steps down
# from green, so they do not share a hue.
BAND_TERMINAL_COLOR: dict[HealthBand, str] = {
    "excellent": "green",
    "good": "green",
    "fair": "yellow",
    "needs_work": "dark_orange",
    "at_risk": "red",
}

# shields.io colour names for the health badge. Excellent and Good share the
# one green, as they do on every other surface.
BAND_BADGE_COLOR: dict[HealthBand, str] = {
    "excellent": "brightgreen",
    "good": "brightgreen",
    "fair": "yellow",
    "needs_work": "orange",
    "at_risk": "red",
}


def band_for(score: float) -> HealthBand:
    """Map a 1-10 score to its band."""
    if score >= EXCELLENT_MIN:
        return "excellent"
    if score >= GOOD_MIN:
        return "good"
    if score >= FAIR_MIN:
        return "fair"
    if score >= NEEDS_WORK_MIN:
        return "needs_work"
    return "at_risk"


def distribution(metrics: list[Any]) -> dict[str, Any]:
    """NLOC-weighted file distribution across the bands.

    Returns the wire shape consumed by ``HealthDistribution`` in
    ``packages/types``: per-band file count, summed NLOC, and the NLOC-weighted
    percentage (0-100, rounded to 1 dp). NLOC is floored at 1 per file so a
    zero-NLOC file still counts once - mirroring the KPI weighting in
    ``scoring.compute_kpis``. An empty repo yields all-zero bands.
    """
    bands: dict[str, dict[str, float]] = {b: {"files": 0, "nloc": 0} for b in BAND_ORDER}
    total_files = 0
    total_weight = 0
    for m in metrics:
        if field(m, "file_path") is None:
            continue
        score = float(field(m, "score", 10.0))
        weight = max(int(field(m, "nloc", 0) or 0), 1)
        band = band_for(score)
        bands[band]["files"] += 1
        bands[band]["nloc"] += weight
        total_files += 1
        total_weight += weight

    out_bands: dict[str, dict[str, Any]] = {}
    for b in BAND_ORDER:
        nloc = int(bands[b]["nloc"])
        pct = round(100.0 * nloc / total_weight, 1) if total_weight else 0.0
        out_bands[b] = {"files": int(bands[b]["files"]), "nloc": nloc, "pct": pct}

    return {
        "total_files": total_files,
        "total_nloc": total_weight,
        "bands": out_bands,
    }

"""Health bands + repo distribution — the "currency" layer over the score.

The 1-10 health score is the single number we surface. On top of it we put ONE
categorical scheme: the 3 defect-backed buckets **Healthy / Warning / Alert**.
Their cutoffs are not arbitrary - on our calibration corpus Alert files carry
roughly 17x the per-file defect rate of Healthy files, so the boundaries are
empirically defensible (see ``docs/CODE_HEALTH.md``).

This module is the SINGLE SOURCE OF TRUTH for the cutoffs. The TypeScript
mirror lives in ``packages/types/src/health.ts`` (``ALERT_MAX`` / ``HEALTHY_MIN``
/ ``bandForScore``) and a parity test on each side locks the values. We
deliberately ship NO letter grade - a letter on top of the number plus the band
would be a third overlapping scale with arbitrary cliffs.

Inputs are duck-typed (mapping ``m["score"]`` or object ``m.score``) so the same
functions serve the server (dict rows), the CLI (``HealthFileMetricData``), and
the hosted backend (``health.json`` dicts) without per-call-site adapters - the
same convention as ``defect_accuracy.py``.
"""

from __future__ import annotations

from typing import Any, Literal

HealthBand = Literal["healthy", "warning", "alert"]

# Canonical band cutoffs. Frozen (scoring is frozen; this is presentation).
# Score >= HEALTHY_MIN -> Healthy; score < ALERT_MAX -> Alert; between -> Warning.
HEALTHY_MIN = 8.0
ALERT_MAX = 4.0

BAND_LABEL: dict[HealthBand, str] = {
    "healthy": "Healthy",
    "warning": "Warning",
    "alert": "Alert",
}

# Ordered worst-first, matching how the surface lists files.
BAND_ORDER: tuple[HealthBand, ...] = ("alert", "warning", "healthy")


def band_for(score: float) -> HealthBand:
    """Map a 1-10 score to its defect-backed band."""
    if score < ALERT_MAX:
        return "alert"
    if score < HEALTHY_MIN:
        return "warning"
    return "healthy"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute-bearing object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def distribution(metrics: list[Any]) -> dict[str, Any]:
    """NLOC-weighted file distribution across the 3 bands.

    Returns the wire shape consumed by ``HealthDistribution`` in
    ``packages/types``: per-band file count, summed NLOC, and the NLOC-weighted
    percentage (0-100, rounded to 1 dp). NLOC is floored at 1 per file so a
    zero-NLOC file still counts once - mirroring the KPI weighting in
    ``scoring.compute_kpis``. An empty repo yields all-zero bands.
    """
    bands: dict[str, dict[str, float]] = {
        b: {"files": 0, "nloc": 0} for b in ("healthy", "warning", "alert")
    }
    total_files = 0
    total_weight = 0
    for m in metrics:
        if _get(m, "file_path") is None:
            continue
        score = float(_get(m, "score", 10.0))
        weight = max(int(_get(m, "nloc", 0) or 0), 1)
        band = band_for(score)
        bands[band]["files"] += 1
        bands[band]["nloc"] += weight
        total_files += 1
        total_weight += weight

    out_bands: dict[str, dict[str, Any]] = {}
    for b in ("healthy", "warning", "alert"):
        nloc = int(bands[b]["nloc"])
        pct = round(100.0 * nloc / total_weight, 1) if total_weight else 0.0
        out_bands[b] = {"files": int(bands[b]["files"]), "nloc": nloc, "pct": pct}

    return {
        "total_files": total_files,
        "total_nloc": total_weight,
        "bands": out_bands,
    }

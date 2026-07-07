"""Backward-compatible re-export; the module lives in core now."""

from repowise.core.cost_estimator.calibration import load_telemetry_averages

__all__ = ["load_telemetry_averages"]

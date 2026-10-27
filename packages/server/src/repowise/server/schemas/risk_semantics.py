"""Typed wire metadata shared by public risk-report schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RiskScaleRange(BaseModel):
    minimum: float | None
    maximum: float | None


class RiskCalibration(BaseModel):
    status: str
    source: str | None = None
    calibrated_at: str | None = None
    population: str | None = None
    granularity: str | None = None


class RiskScalarSemantics(BaseModel):
    field: str
    kind: str
    unit: str
    range: RiskScaleRange | None
    measures: str
    deterministic: bool = True
    calibration: RiskCalibration | None = None
    authoritative: bool | None = None
    authoritative_for_change_review: bool | None = None
    runtime_breakage_probability: bool | None = None
    formula: str | None = None
    thresholds: dict[str, float] | None = None
    band_thresholds: dict[str, float] | None = None
    component_fields: dict[str, dict[str, Any]] | None = None


class RiskAuthority(BaseModel):
    authoritative_for: str
    primary_fields: list[str]
    primary_basis: str
    fallback_field: str
    fallback_basis: str
    score_role: str


class RiskCompatibilityField(BaseModel):
    deprecated: bool
    replacement: str
    equivalent_value: bool
    historical_meaning: str

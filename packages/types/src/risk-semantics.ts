/** Shared machine-readable vocabulary for public risk and impact scalars. */

export interface RiskScaleRange {
  minimum: number | null;
  maximum: number | null;
}

export interface RiskCalibration {
  status: "benchmarked" | "heuristic_thresholds" | "uncalibrated" | "not_applicable";
  /** Reference tier: present only under include=["scales"]. */
  source?: string | null;
  calibrated_at?: string | null;
  population?: string | null;
  granularity?: string | null;
}

export interface RiskScalarSemantics {
  field: string;
  kind: string;
  unit: string;
  range: RiskScaleRange | null;
  measures: string;
  /** Reference tier: present only under include=["scales"]. */
  deterministic?: boolean;
  calibration?: RiskCalibration | null;
  authoritative?: boolean | null;
  authoritative_for_change_review?: boolean | null;
  runtime_breakage_probability?: boolean | null;
  formula?: string | null;
  thresholds?: Record<string, number> | null;
  band_thresholds?: Record<string, number> | null;
  component_fields?: Record<string, Record<string, unknown>> | null;
}

export interface RiskAuthority {
  authoritative_for: "live_change_review";
  primary_fields: ["risk_percentile", "classification"];
  primary_basis: "benchmarked_population_relative";
  fallback_field: "fallback_band";
  fallback_basis: "absolute_model_score_band";
  score_role: "supporting_diff_shape_signal";
}

export interface RiskCompatibilityField {
  deprecated: true;
  replacement: string;
  equivalent_value: true;
  historical_meaning: string;
}

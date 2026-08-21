/** Canonical wire contract for structured refactoring recommendations. */

import type { Paginated } from "./pagination.js";

export type RefactoringType =
  | "extract_class"
  | "extract_helper"
  | "extract_method"
  | "move_method"
  | "break_cycle"
  | "split_file"
  | "performance_fix";

export type EffortBucket = "S" | "M" | "L" | "XL";
export type Confidence = "low" | "medium" | "high";
export type ValidationBasis = "measured" | "inferred" | "mixed" | "unknown";
export type ValidationVia = "coverage" | "call-graph" | "import-graph" | "mixed";

export interface RecommendationValidationTarget {
  file_path: string;
  basis: ValidationBasis;
  via: ValidationVia | null;
  total: number;
  tests: string[];
  truncated: boolean;
}

export interface RecommendationValidation {
  basis: ValidationBasis;
  via: ValidationVia | null;
  total: number;
  tests: string[];
  truncated: boolean;
  affected_files: string[];
  affected_symbols: string[];
  commands: string[];
  targets: RecommendationValidationTarget[];
}

export interface RefactoringPlan {
  id: string;
  refactoring_type: RefactoringType | string;
  file_path: string;
  target_symbol: string;
  line_start: number | null;
  line_end: number | null;
  plan: Record<string, unknown>;
  evidence: Record<string, unknown>;
  impact_delta: number;
  effort_bucket: EffortBucket | string;
  blast_radius: Record<string, unknown>;
  confidence: Confidence | string;
  source_biomarker: string;
  /** Compatibility priority. Older servers already provide this field. */
  rank_score: number;
  /** Optional so a newer client can read a payload from a server that predates them. */
  benefit?: number;
  leverage?: number;
  cost?: number;
  risk?: number;
  dependents?: number;
  file_nloc?: number;
  file_weighted_deficit?: number;
  validation?: RecommendationValidation;
}

export interface RefactoringTypeCount {
  type: string;
  count: number;
}

export interface RefactoringSummary {
  total: number;
  by_type: RefactoringTypeCount[];
  /** Additive Phase 4 aggregates; absent on older servers. */
  files_total?: number;
  structural_total?: number;
  performance_total?: number;
  small_effort_total?: number;
  health_recovery_total?: number;
  negligible_health_total?: number;
  best_health_gain?: number;
}

export interface RefactoringTargets {
  summary: RefactoringSummary;
  plans: RefactoringPlan[];
}

/** Bounded product list. The legacy unpaged RefactoringTargets path remains. */
export interface RefactoringPlanPage extends Paginated<RefactoringPlan> {
  summary: RefactoringSummary;
  /** Bounded canonical structural head used by the existing Start here section. */
  structural_leads: RefactoringPlan[];
}

export interface GeneratedSpan {
  file: string;
  line_start: number;
  line_end: number;
}

export interface GeneratedCode {
  suggestion_id: string | null;
  refactoring_type: string;
  file_path: string;
  target_symbol: string;
  content: string;
  diff: string;
  provider: string;
  model: string;
  cached: boolean;
  input_tokens: number;
  output_tokens: number;
  validation: Record<string, unknown>;
  spans: GeneratedSpan[];
}

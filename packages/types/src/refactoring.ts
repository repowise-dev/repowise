/** Canonical wire contract for structured refactoring recommendations. */

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
}

export interface RefactoringTargets {
  summary: RefactoringSummary;
  plans: RefactoringPlan[];
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

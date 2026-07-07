// ---------------------------------------------------------------------------
// Dead Code
// ---------------------------------------------------------------------------

import type { DeadCodeStatus } from "@repowise-dev/types/dead-code";

export interface DeadCodeFindingResponse {
  id: string;
  kind: string;
  file_path: string;
  symbol_name: string | null;
  symbol_kind: string | null;
  confidence: number;
  reason: string;
  lines: number;
  start_line: number | null;
  end_line: number | null;
  /** Effective deletion-readiness — re-derived from confidence + risk factors. */
  safe_to_delete: boolean;
  /** Runtime-load risk factors that make this a review candidate, not a delete. */
  risk_factors: string[];
  /** Human-readable signals behind the finding (no-importers, age, risk). */
  evidence: string[];
  primary_owner: string | null;
  status: DeadCodeStatus;
  note: string | null;
}

export interface DeadCodePatchRequest {
  status: DeadCodeStatus;
  note?: string;
}

export interface DeadCodeSummaryResponse {
  total_findings: number;
  confidence_summary: Record<string, number>;
  deletable_lines: number;
  total_lines: number;
  by_kind: Record<string, number>;
}

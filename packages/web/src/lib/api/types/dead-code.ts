// ---------------------------------------------------------------------------
// Dead Code
// ---------------------------------------------------------------------------

export interface DeadCodeFindingResponse {
  id: string;
  kind: string;
  file_path: string;
  symbol_name: string | null;
  symbol_kind: string | null;
  confidence: number;
  reason: string;
  lines: number;
  safe_to_delete: boolean;
  primary_owner: string | null;
  status: string;
  note: string | null;
}

export interface DeadCodePatchRequest {
  status: string;
  note?: string;
}

export interface DeadCodeSummaryResponse {
  total_findings: number;
  confidence_summary: Record<string, number>;
  deletable_lines: number;
  total_lines: number;
  by_kind: Record<string, number>;
}

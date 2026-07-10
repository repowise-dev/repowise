// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

export interface PageResponse {
  id: string;
  repository_id: string;
  page_type: string;
  title: string;
  content: string;
  target_path: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  generation_level: number;
  version: number;
  confidence: number;
  freshness_status: string;
  metadata: Record<string, unknown>;
  human_notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PageVersionResponse {
  id: string;
  page_id: string;
  version: number;
  page_type: string;
  title: string;
  content: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  confidence: number;
  archived_at: string;
}

export interface PageListResponse {
  pages: PageResponse[];
  total: number;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export interface JobResponse {
  id: string;
  repository_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "paused";
  provider_name: string;
  model_name: string;
  total_pages: number;
  completed_pages: number;
  failed_pages: number;
  current_level: number;
  error_message: string | null;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobProgressEvent {
  event: "progress" | "done" | "error";
  job_id: string;
  status?: "pending" | "running" | "completed" | "failed" | "cancelled" | "paused";
  completed_pages: number;
  total_pages: number;
  failed_pages?: number;
  current_page?: string;
  current_level?: number;
  /** Human phase label ("Parsing files", "Generating docs") from the pipeline. */
  phase?: string;
  tokens_input?: number;
  tokens_output?: number;
  estimated_cost?: number;
  actual_cost_usd?: number | null;
  error_message?: string | null;
  error?: string;
}

/** An `event: message` frame on the job SSE stream: one pipeline log line. */
export interface JobMessageEvent {
  seq: number;
  ts: number;
  level: string;
  text: string;
  phase: string;
}

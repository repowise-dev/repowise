// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

/**
 * What `GET /api/pages?fields=summary` returns: every page field except the
 * two heavy ones. On a large wiki `content` and `metadata` are 95% of a full
 * listing's bytes, and nothing that draws a list of pages reads either.
 */
export interface PageSummary {
  id: string;
  repository_id: string;
  page_type: string;
  title: string;
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
  /** Length of the omitted `content`, so a list can rank pages by how much was
   *  written without carrying the writing. Optional: a backend predating the
   *  field just omits it, and callers fall back rather than ranking on
   *  `undefined`. */
  content_chars?: number;
  /** Present only when this row was fetched in full — a caller is free to
   *  hydrate a few rows of an otherwise summary listing. */
  content?: string;
  metadata?: Record<string, unknown>;
  /** Which layer of the architecture spine claims this page, stamped at
   *  generation time. Promoted out of `metadata` because a list needs it: the
   *  docs tree groups modules under their layer from this stamp, and a listing
   *  drops the blob. Absent or `null` means no layer claimed the page. */
  layer_id?: string | null;
  layer_name?: string | null;
  human_notes: string | null;
  created_at: string;
  updated_at: string;
  /** Position in the wiki outline, computed once at generation time so every
   *  reader navigates the same tree. Optional: pages written before the wiki
   *  carried a tree have no placement, which reads as flat. */
  parent_page_id?: string | null;
  display_order?: number;
  section_number?: string | null;
  structural_key?: string | null;
}

export interface PageResponse extends PageSummary {
  content: string;
  metadata: Record<string, unknown>;
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
  /** Short-lived token authorizing this job's SSE stream (an EventSource can't
   * send the bearer header). Present only while the job is live; pass it as
   * `?token=` on the stream URL. */
  stream_token?: string | null;
}

/** What a launch endpoint (`/generate`, `/pages/lookup/regenerate`, `/index`)
 *  returns: the new job's id plus a short-lived token for its SSE stream. */
export interface JobLaunchResponse {
  job_id: string;
  status: string;
  /** Present while the job is live; pass as `?token=` on the stream URL. */
  stream_token?: string | null;
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

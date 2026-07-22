// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  db: string;
  version: string;
}

// ---------------------------------------------------------------------------
// Webhooks
// ---------------------------------------------------------------------------

export interface WebhookResponse {
  event_id: string;
  status: string;
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

export interface ProviderInfo {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  configured: boolean;
}

export interface ProvidersResponse {
  active: {
    provider: string | null;
    model: string | null;
  };
  providers: ProviderInfo[];
}

/** Result of a live provider smoke test. `ok: false` carries the reason in
 *  `error` — the endpoint never throws for a bad key. */
export interface ProviderValidation {
  ok: boolean;
  provider: string | null;
  model: string | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Scoped generation (POST /repos/{id}/generate[/estimate])
// ---------------------------------------------------------------------------

export type GenerateCascade = "none" | "dependents" | "full";

/** Which pages a generate/estimate call targets. Mirrors the server's
 *  `GenerateSelectionBody`. */
export interface GenerateSelection {
  kind: "all" | "unwritten" | "stale" | "page_ids" | "path_prefix";
  page_ids?: string[];
  path_prefix?: string;
}

export interface GenerateRequest {
  selection?: GenerateSelection;
  cascade?: GenerateCascade;
  style?: string;
}

/** Cost + page counts for a generate selection, cascade fallout included.
 *  `estimate` is null when no provider resolves (nothing to price). */
export interface GenerateEstimate {
  total_pages: number;
  pages_by_type: Record<string, number>;
  pages_to_mark_stale: number;
  unknown_page_ids: string[];
  provider: { name: string | null; model: string | null; error: string | null };
  estimate: {
    estimated_cost_usd: number;
    cost_low_usd: number | null;
    cost_high_usd: number | null;
    estimated_input_tokens: number;
    estimated_output_tokens: number;
    is_calibrated: boolean;
  } | null;
  note?: string | null;
}

// ---------------------------------------------------------------------------
// API error
// ---------------------------------------------------------------------------

export interface ApiError {
  detail: string;
  status: number;
}

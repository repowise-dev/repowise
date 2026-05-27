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

// ---------------------------------------------------------------------------
// API error
// ---------------------------------------------------------------------------

export interface ApiError {
  detail: string;
  status: number;
}

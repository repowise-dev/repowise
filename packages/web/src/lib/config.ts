/**
 * Settings persisted in localStorage.
 * Keys are prefixed with "repowise_".
 * All helpers are safe to call in SSR — they return defaults when window is undefined.
 */

const KEYS = {
  apiKey: "repowise_api_key",
  apiUrl: "repowise_api_url",
  provider: "repowise_default_provider",
  model: "repowise_default_model",
  embedder: "repowise_embedder",
  weekend: "repowise_weekend",
} as const;

function read(key: string): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(key) ?? "";
}

function write(key: string, value: string): void {
  if (typeof window === "undefined") return;
  if (value) {
    localStorage.setItem(key, value);
  } else {
    localStorage.removeItem(key);
  }
}

export const config = {
  getApiKey: () => read(KEYS.apiKey),
  setApiKey: (v: string) => write(KEYS.apiKey, v),

  getApiUrl: () => read(KEYS.apiUrl),
  setApiUrl: (v: string) => write(KEYS.apiUrl, v),

  getProvider: () => read(KEYS.provider) || "litellm",
  setProvider: (v: string) => write(KEYS.provider, v),

  getModel: () => read(KEYS.model),
  setModel: (v: string) => write(KEYS.model, v),

  getEmbedder: () => read(KEYS.embedder) || "mock",
  setEmbedder: (v: string) => write(KEYS.embedder, v),

  /** Weekend-days preset id; "" means unset, which resolves to Sat/Sun. */
  getWeekend: () => read(KEYS.weekend),
  setWeekend: (v: string) => write(KEYS.weekend, v),
};

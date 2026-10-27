/**
 * Provider management API module.
 */

import { apiGet, apiPatch, apiPost, apiDelete } from "./client";
import type {
  ProviderModels,
  ProvidersResponse,
  ProviderValidation,
} from "./types";

/** `detect` also asks each agent CLI whether it is signed in, which fills
 *  `detected: "signed_in"`; it runs a short process per CLI, so only the
 *  surfaces that offer sources ask for it. */
export async function getProviders(
  repoId?: string,
  options: { detect?: boolean } = {},
): Promise<ProvidersResponse> {
  const params = new URLSearchParams();
  if (repoId) params.set("repo_id", repoId);
  if (options.detect) params.set("detect", "true");
  const qs = params.toString();
  return apiGet<ProvidersResponse>(`/api/providers${qs ? `?${qs}` : ""}`);
}

/** Set the provider and model (any id the provider accepts). With `repoId`
 *  the choice is written to that repo's `.repowise/config.yaml`, which the CLI
 *  reads too; without it, the server-wide default. `baseUrl` is an http(s)
 *  endpoint such as an OpenAI-compatible LM Studio server: omit it to keep the
 *  current one, pass `""` to clear it. */
export async function setActiveProvider(
  provider: string,
  model?: string,
  repoId?: string,
  options: { baseUrl?: string } = {},
): Promise<ProvidersResponse> {
  return apiPatch<ProvidersResponse>("/api/providers/active", {
    provider,
    model: model ?? null,
    repo_id: repoId ?? null,
    ...(options.baseUrl !== undefined ? { base_url: options.baseUrl } : {}),
  });
}

/** The provider's models, live where it can list them, else its built-in
 *  list with `error` set. `baseUrl` tests an endpoint before it is saved (no
 *  stored key is sent to it); `refresh` skips the server's short cache. */
export async function getProviderModels(
  providerId: string,
  options: { repoId?: string; baseUrl?: string; refresh?: boolean } = {},
): Promise<ProviderModels> {
  const params = new URLSearchParams();
  if (options.repoId) params.set("repo_id", options.repoId);
  if (options.baseUrl) params.set("base_url", options.baseUrl);
  if (options.refresh) params.set("refresh", "true");
  const qs = params.size ? `?${params}` : "";
  return apiGet<ProviderModels>(
    `/api/providers/${encodeURIComponent(providerId)}/models${qs}`,
  );
}

/** Store a key for a provider. Passing `repoId` also mirrors it into that
 *  repo's `.repowise/.env` (D6), so a later CLI run in the repo picks it up —
 *  without it the key stays server-global and the CLI never sees it. */
export async function addProviderKey(
  providerId: string,
  apiKey: string,
  repoId?: string,
): Promise<void> {
  await apiPost(`/api/providers/${providerId}/key`, {
    api_key: apiKey,
    repo_id: repoId ?? null,
  });
}

/** Remove a provider's key. Passing `repoId` also clears it from that repo's
 *  `.repowise/.env` mirror. */
export async function removeProviderKey(
  providerId: string,
  repoId?: string,
): Promise<void> {
  const qs = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : "";
  await apiDelete(`/api/providers/${providerId}/key${qs}`);
}

/** Live smoke test for a single provider's configured key. Never throws for a
 *  bad key — the failure is reported in `ok` / `error`. */
export async function validateProviderKey(
  providerId: string,
  repoId?: string,
): Promise<ProviderValidation> {
  const qs = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : "";
  return apiPost<ProviderValidation>(
    `/api/providers/${providerId}/validate${qs}`,
  );
}

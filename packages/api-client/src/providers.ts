/**
 * Provider management API module.
 */

import { apiGet, apiPatch, apiPost, apiDelete } from "./client";
import type { ProvidersResponse, ProviderValidation } from "./types";

export async function getProviders(
  repoId?: string,
): Promise<ProvidersResponse> {
  const qs = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : "";
  return apiGet<ProvidersResponse>(`/api/providers${qs}`);
}

export async function setActiveProvider(
  provider: string,
  model?: string,
  repoId?: string,
): Promise<ProvidersResponse> {
  return apiPatch<ProvidersResponse>("/api/providers/active", {
    provider,
    model: model ?? null,
    repo_id: repoId ?? null,
  });
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

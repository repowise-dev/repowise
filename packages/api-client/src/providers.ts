/**
 * Provider management API module.
 */

import { apiGet, apiPatch, apiPost, apiDelete } from "./client";
import type { ProvidersResponse } from "./types";

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

export async function addProviderKey(
  providerId: string,
  apiKey: string,
): Promise<void> {
  await apiPost(`/api/providers/${providerId}/key`, { api_key: apiKey });
}

export async function removeProviderKey(providerId: string): Promise<void> {
  await apiDelete(`/api/providers/${providerId}/key`);
}

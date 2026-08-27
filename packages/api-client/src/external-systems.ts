import type {
  ExternalSystemsRegistry,
  ExternalSystemsSummary,
  ExternalSystemsSummaryScope,
} from "@repowise-dev/types/external-systems";

import { apiGet } from "./client";

/** Full dependency registry (one row per name + manifest, undeduplicated). */
export async function getExternalSystems(
  repoId: string,
): Promise<ExternalSystemsRegistry> {
  return apiGet<ExternalSystemsRegistry>(
    `/api/repos/${repoId}/external-systems`,
  );
}

/** Canonical packages with bounded declaration and persisted usage aggregates. */
export async function getExternalSystemsSummary(
  repoId: string,
  options: {
    scope?: ExternalSystemsSummaryScope;
    limit?: number;
    offset?: number;
  } = {},
): Promise<ExternalSystemsSummary> {
  const params = new URLSearchParams();
  if (options.scope) params.set("scope", options.scope);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  const query = params.size ? `?${params.toString()}` : "";
  return apiGet<ExternalSystemsSummary>(
    `/api/repos/${repoId}/external-systems/summary${query}`,
  );
}

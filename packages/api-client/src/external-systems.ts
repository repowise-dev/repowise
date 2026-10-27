import type {
  ExternalSystemsRegistry,
  ExternalSystemImportingFiles,
  ExternalSystemRelationshipGraph,
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

/** Aggregate-first focused relationship graph for one selected package. */
export async function getExternalSystemRelationshipGraph(
  repoId: string,
  packageKey: string,
  options: { scope?: ExternalSystemsSummaryScope; nodeLimit?: number; edgeLimit?: number } = {},
): Promise<ExternalSystemRelationshipGraph> {
  const params = new URLSearchParams();
  if (options.scope) params.set("scope", options.scope);
  if (options.nodeLimit !== undefined) params.set("node_limit", String(options.nodeLimit));
  if (options.edgeLimit !== undefined) params.set("edge_limit", String(options.edgeLimit));
  const query = params.size ? `?${params.toString()}` : "";
  return apiGet<ExternalSystemRelationshipGraph>(
    `/api/repos/${repoId}/external-systems/${encodeURIComponent(packageKey)}/graph${query}`,
  );
}

/** One independently bounded page of importing files for an aggregate. */
export async function getExternalSystemImportingFiles(
  repoId: string,
  packageKey: string,
  aggregateKey: string,
  options: { scope?: ExternalSystemsSummaryScope; limit?: number; offset?: number } = {},
): Promise<ExternalSystemImportingFiles> {
  const params = new URLSearchParams({ aggregate_key: aggregateKey });
  if (options.scope) params.set("scope", options.scope);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  return apiGet<ExternalSystemImportingFiles>(
    `/api/repos/${repoId}/external-systems/${encodeURIComponent(packageKey)}/graph/files?${params.toString()}`,
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

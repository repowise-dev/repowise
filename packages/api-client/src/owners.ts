import type { IdentityKind, KnowledgeMatrix, OwnersSummary } from "@repowise-dev/types/owners";
import { apiGet } from "./client";
import { fetchAllPaginated } from "./pagination";
import type {
  OwnerListEntry,
  OwnerProfileResponse,
  Paginated,
} from "./types";

export type OwnerSortKey =
  | "files_owned"
  | "hotspots_owned"
  | "commit_count_90d"
  | "dead_code_lines_owned"
  | "bus_factor_risk_files";

export interface ListOwnersParams {
  repoId: string;
  q?: string;
  sort?: OwnerSortKey;
  /** Only this kind of identity; omitted lists every kind. */
  kind?: IdentityKind;
  limit?: number;
  offset?: number;
}

export async function listOwnersPage(
  params: ListOwnersParams,
): Promise<Paginated<OwnerListEntry>> {
  const { repoId, ...rest } = params;
  return apiGet<Paginated<OwnerListEntry>>(`/api/repos/${repoId}/owners`, rest);
}

/** Fetch every owner row by walking pagination — for charts/export surfaces. */
export async function listAllOwners(
  params: Omit<ListOwnersParams, "limit" | "offset"> & {
    pageSize?: number;
    maxItems?: number;
  },
): Promise<OwnerListEntry[]> {
  const { repoId, pageSize, maxItems, ...rest } = params;
  return fetchAllPaginated({
    fetchPage: (offset, limit) => listOwnersPage({ repoId, ...rest, offset, limit }),
    ...(pageSize !== undefined ? { pageSize } : {}),
    ...(maxItems !== undefined ? { maxItems } : {}),
  });
}

export async function getOwnerProfile(
  repoId: string,
  ownerKey: string,
): Promise<OwnerProfileResponse> {
  return apiGet<OwnerProfileResponse>(
    `/api/repos/${repoId}/owners/${encodeURIComponent(ownerKey)}`,
  );
}

/** Truck factor, silos, concentration and activity; people only, built once per index. */
export async function getOwnersSummary(repoId: string): Promise<OwnersSummary> {
  return apiGet<OwnersSummary>(`/api/repos/${repoId}/owners/summary`);
}

/** Every code file's top authors and risk facts, for client-side off-boarding. */
export async function getKnowledgeMatrix(repoId: string): Promise<KnowledgeMatrix> {
  return apiGet<KnowledgeMatrix>(`/api/repos/${repoId}/owners/knowledge-matrix`);
}

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

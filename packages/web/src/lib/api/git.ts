import { apiGet } from "./client";
import type {
  GitMetadataResponse,
  GitSummaryResponse,
  HotspotResponse,
  OwnershipEntry,
  Paginated,
} from "./types";

export async function getGitMetadata(
  repoId: string,
  filePath: string,
): Promise<GitMetadataResponse> {
  return apiGet<GitMetadataResponse>(`/api/repos/${repoId}/git-metadata`, {
    file_path: filePath,
  });
}

/**
 * Paginated hotspot fetch — returns the full envelope so the UI can render
 * "Showing N of M / Load more". New callers should prefer this over
 * {@link getHotspots}, which is kept for back-compat (returns just the
 * items list, but no longer hides the long tail since the server cap is
 * 500 rather than the old 100).
 */
export async function getHotspotsPage(
  repoId: string,
  options: { limit?: number; offset?: number } = {},
): Promise<Paginated<HotspotResponse>> {
  const { limit = 50, offset = 0 } = options;
  return apiGet<Paginated<HotspotResponse>>(`/api/repos/${repoId}/hotspots`, {
    limit,
    offset,
  });
}

/**
 * Back-compat helper — keeps the legacy `(repoId, limit) → list` signature
 * used by widgets that don't (yet) render a "load more" affordance.
 */
export async function getHotspots(
  repoId: string,
  limit = 50,
): Promise<HotspotResponse[]> {
  const page = await getHotspotsPage(repoId, { limit });
  return page.items;
}

export async function getOwnershipPage(
  repoId: string,
  granularity: "file" | "module" = "module",
  options: { limit?: number; offset?: number } = {},
): Promise<Paginated<OwnershipEntry>> {
  const { limit = 500, offset = 0 } = options;
  return apiGet<Paginated<OwnershipEntry>>(`/api/repos/${repoId}/ownership`, {
    granularity,
    limit,
    offset,
  });
}

export async function getOwnership(
  repoId: string,
  granularity: "file" | "module" = "module",
): Promise<OwnershipEntry[]> {
  const page = await getOwnershipPage(repoId, granularity, { limit: 5000 });
  return page.items;
}

export async function getCoChanges(
  repoId: string,
  filePath: string,
  minCount = 3,
): Promise<{
  file_path: string;
  co_change_partners: Array<{ file_path: string; co_change_count: number }>;
  total?: number;
}> {
  return apiGet(`/api/repos/${repoId}/co-changes`, {
    file_path: filePath,
    min_count: minCount,
  });
}

export async function getGitSummary(
  repoId: string,
  topOwnersLimit?: number,
): Promise<GitSummaryResponse> {
  const params = topOwnersLimit ? { top_owners_limit: topOwnersLimit } : undefined;
  return apiGet<GitSummaryResponse>(`/api/repos/${repoId}/git-summary`, params);
}

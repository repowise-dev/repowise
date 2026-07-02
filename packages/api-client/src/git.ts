import { apiGet } from "./client";
import type {
  AgentTrend,
  CommitEvolution,
  CommitStats,
  CommitDetailResponse,
  CommitResponse,
  GitMetadataResponse,
  GitSummaryResponse,
  HotspotResponse,
  OwnershipEntry,
  Paginated,
  ReviewerSuggestionsResponse,
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

export async function getReviewerSuggestions(
  repoId: string,
  paths: string[],
  limit = 10,
): Promise<ReviewerSuggestionsResponse> {
  // FastAPI repeats `?paths=` per entry; URLSearchParams handles that for
  // us when we hand it tuples.
  const sp = new URLSearchParams();
  for (const p of paths) sp.append("paths", p);
  sp.append("limit", String(limit));
  return apiGet<ReviewerSuggestionsResponse>(
    `/api/repos/${repoId}/reviewer-suggestions?${sp.toString()}`,
  );
}

export async function getGitSummary(
  repoId: string,
  topOwnersLimit?: number,
): Promise<GitSummaryResponse> {
  const params = topOwnersLimit ? { top_owners_limit: topOwnersLimit } : undefined;
  return apiGet<GitSummaryResponse>(`/api/repos/${repoId}/git-summary`, params);
}

/**
 * Paginated per-commit change-risk feed — the review-priority queue. `sort`
 * defaults to `risk` (review-priority order); `date` orders by recency. Each
 * commit carries a repo-relative `risk_percentile` + `review_priority`.
 */
export async function getCommitsPage(
  repoId: string,
  options: {
    sort?: "risk" | "date";
    authorship?: "all" | "agent" | "human";
    limit?: number;
    offset?: number;
  } = {},
): Promise<Paginated<CommitResponse>> {
  const { sort = "risk", authorship = "all", limit = 50, offset = 0 } = options;
  return apiGet<Paginated<CommitResponse>>(`/api/repos/${repoId}/commits`, {
    sort,
    authorship,
    limit,
    offset,
  });
}

/** Monthly agent-vs-human commit volume across the indexed window. */
export async function getAgentTrend(repoId: string): Promise<AgentTrend> {
  return apiGet<AgentTrend>(`/api/repos/${repoId}/commits/agent-trend`);
}

export async function getCommitEvolution(
  repoId: string,
  granularity: "auto" | "month" | "week" = "auto",
): Promise<CommitEvolution> {
  return apiGet<CommitEvolution>(
    `/api/repos/${repoId}/commits/evolution?granularity=${granularity}`,
  );
}

export async function getCommitStats(repoId: string): Promise<CommitStats> {
  return apiGet<CommitStats>(`/api/repos/${repoId}/commits/stats`);
}

export async function getCommit(
  repoId: string,
  sha: string,
): Promise<CommitDetailResponse> {
  return apiGet<CommitDetailResponse>(`/api/repos/${repoId}/commits/${sha}`);
}

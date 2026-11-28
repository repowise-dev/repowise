import { holdParams } from "./areas";
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
import type { MapHoldQuery } from "@repowise-dev/types/areas";
import type {
  HotspotFacets,
  HotspotFilter,
  HotspotOrder,
  HotspotSort,
} from "@repowise-dev/types/git";
import type {
  HistoryFilters,
  PeriodHealthSummaryResponse,
} from "@repowise-dev/types/history";

export async function getGitMetadata(
  repoId: string,
  filePath: string,
): Promise<GitMetadataResponse> {
  return apiGet<GitMetadataResponse>(`/api/repos/${repoId}/git-metadata`, {
    file_path: filePath,
  });
}

/** One page of the hotspot list plus counts over the whole searched set. */
export interface HotspotPage extends Paginated<HotspotResponse> {
  /** Absent from a server that does not count filters; the list renders without chips. */
  facets?: HotspotFacets;
}

export interface HotspotPageQuery extends MapHoldQuery {
  limit?: number;
  offset?: number;
  /** Path or owner substring. */
  q?: string;
  filter?: HotspotFilter;
  sort?: HotspotSort;
  order?: HotspotOrder;
}

/**
 * One page of hotspots ranked by temporal hotspot score. `total` counts what
 * the filter and search match; `facets` count every filter over the search.
 * Defaults (no search, no filter, every origin) list the whole hotspot set.
 */
export async function getHotspotsPage(
  repoId: string,
  options: HotspotPageQuery = {},
): Promise<HotspotPage> {
  const { limit = 50, offset = 0, q, filter, sort, order } = options;
  return apiGet<HotspotPage>(`/api/repos/${repoId}/hotspots`, {
    limit,
    offset,
    q: q || undefined,
    filter: filter && filter !== "all" ? filter : undefined,
    sort,
    order,
    ...holdParams(options),
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
 * Paginated per-commit feed, each row led by its `health_delta` and
 * `touches_hotspot`. `sort` defaults to `date`; `risk` orders by the
 * supporting diff-shape score. The history filters narrow the repository.
 */
export async function getCommitsPage(
  repoId: string,
  options: HistoryFilters & {
    sort?: "risk" | "date";
    authorship?: "all" | "agent" | "human" | "unknown";
    /** Narrows the whole repository, not the page: the high review-priority
     *  band, or bug-fix commits. */
    kind?: "all" | "high" | "fixes";
    limit?: number;
    offset?: number;
  } = {},
): Promise<Paginated<CommitResponse>> {
  const {
    sort = "date",
    authorship = "all",
    kind = "all",
    limit = 50,
    offset = 0,
    ...filters
  } = options;
  return apiGet<Paginated<CommitResponse>>(`/api/repos/${repoId}/commits`, {
    sort,
    authorship,
    kind,
    limit,
    offset,
    ...filters,
  });
}

/** How many commits under the filters made health better, worse, both or
 *  neither; improvements first. */
export async function getCommitHealthSummary(
  repoId: string,
  filters: HistoryFilters = {},
): Promise<PeriodHealthSummaryResponse> {
  return apiGet<PeriodHealthSummaryResponse>(
    `/api/repos/${repoId}/commits/health-summary`,
    { ...filters },
  );
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

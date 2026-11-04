/**
 * Change-coupling API client. The response types live in the shared
 * `@repowise-dev/types/coupling` contract; this module re-exports them and
 * keeps only the fetch functions.
 */
import type {
  CouplingAreasResponse,
  CouplingFilters,
  CouplingGraphResponse,
} from "@repowise-dev/types/coupling";
import { apiGet } from "./client";

export type {
  CouplingAreaPair,
  CouplingAreaSummary,
  CouplingAreasResponse,
  CouplingEdge,
  CouplingFilters,
  CouplingGraphResponse,
  CouplingNode,
} from "@repowise-dev/types/coupling";

/** The shared filters as query params; a false flag is left off the URL. */
function filterParams(f: CouplingFilters = {}) {
  return {
    depth: f.depth,
    root: f.root || undefined,
    min_support: f.minSupport || undefined,
    unexplained: f.unexplained || undefined,
    cross_area: f.crossArea || undefined,
    exclude_tests: f.excludeTests || undefined,
    recent_days: f.recentDays,
  };
}

export async function getCoupling(
  repoId: string,
  opts: CouplingFilters & {
    limit?: number;
    /** Only pairs with a file in this area... */
    area?: string;
    /** ...and, with `area`, their other file in this one. */
    partner?: string;
  } = {},
): Promise<CouplingGraphResponse> {
  return apiGet<CouplingGraphResponse>(`/api/repos/${repoId}/coupling`, {
    ...filterParams(opts),
    limit: opts.limit,
    area: opts.area,
    partner: opts.partner,
  });
}

/** Which areas change together, ranked by file pairs no dependency explains. */
export async function getCouplingAreas(
  repoId: string,
  opts: CouplingFilters & { limit?: number; topPairs?: number } = {},
): Promise<CouplingAreasResponse> {
  return apiGet<CouplingAreasResponse>(`/api/repos/${repoId}/coupling/areas`, {
    ...filterParams(opts),
    limit: opts.limit,
    top_pairs: opts.topPairs,
  });
}

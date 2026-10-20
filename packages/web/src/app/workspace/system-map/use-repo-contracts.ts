"use client";

import useSWR from "swr";
import type { SystemMapRepoContracts } from "@repowise-dev/ui/workspace/system-map";
import { getWorkspaceContracts } from "@/lib/api/workspace";

/** The endpoint's page ceiling. */
const PAGE = 1000;
/**
 * Pages fetched for one repository. The drawer states when a repository has
 * more contracts than this, and counts from what it has. Ceiling: a repository
 * past 5,000 contracts wants a server-side per-service filter on
 * /api/workspace/contracts instead of client paging.
 */
const MAX_PAGES = 5;

/**
 * Every contract and matched link for one repository, fetched only once the
 * drawer needs it (a service or relationship is selected). Keyed by repo, so
 * the node and edge drawers share one fetch.
 */
export function useRepoContracts(repo: string | null) {
  const { data, isLoading } = useSWR<SystemMapRepoContracts>(
    repo ? `workspace:system-map:repo-contracts:${repo}` : null,
    async () => {
      const first = await getWorkspaceContracts({ repo: repo as string, limit: PAGE });
      const pages = Math.min(Math.ceil(first.total_contracts / PAGE), MAX_PAGES);
      // Later pages in parallel; links are not paged, so the first page's set is complete.
      const rest = await Promise.all(
        Array.from({ length: Math.max(pages - 1, 0) }, (_, i) =>
          getWorkspaceContracts({ repo: repo as string, limit: PAGE, offset: (i + 1) * PAGE }),
        ),
      );
      return {
        contracts: [...first.contracts, ...rest.flatMap((r) => r.contracts)],
        links: first.links,
        total: first.total_contracts,
      };
    },
    { revalidateOnFocus: false },
  );
  return { data: data ?? null, isLoading };
}

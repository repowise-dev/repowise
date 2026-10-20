import type { PerformanceOpportunityPage } from "@repowise-dev/types/health";

import type { PerformanceViewAdapter } from "./adapter";

/**
 * What this server and this host can actually answer.
 *
 * Derived from the response and the adapter, never from a deployment flag. A
 * capability that is missing produces an honest fallback and says so, rather
 * than a control that cannot act.
 */
export interface PerformanceCapabilities {
  /** The server counts the filter vocabulary, so filters can be server-owned. */
  serverFacets: boolean;
  /**
   * The server speaks the four-context vocabulary. When false the tab collapses
   * Production and Tooling into the retired pairing, which is the only case in
   * which that spelling is sent.
   */
  canonicalContexts: boolean;
  /** The host can fetch one opportunity by id, with lifecycle and model state. */
  detailById: boolean;
  /** The host can page raw observations beyond the ones carried on the row. */
  pagedEvidence: boolean;
  /** The host can resolve the exact plan behind a plan id. */
  planById: boolean;
}

export function capabilitiesOf(
  adapter: PerformanceViewAdapter,
  page: PerformanceOpportunityPage | undefined,
): PerformanceCapabilities {
  const facets = page?.facets;
  return {
    serverFacets: Boolean(facets && Object.keys(facets).length > 0),
    canonicalContexts: typeof page?.summary?.status === "string",
    detailById: typeof adapter.getPerformanceOpportunity === "function",
    pagedEvidence: typeof adapter.getPerformanceOpportunityFindings === "function",
    planById: typeof adapter.getRefactoringPlan === "function",
  };
}

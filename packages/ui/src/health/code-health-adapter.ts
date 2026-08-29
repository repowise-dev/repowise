import type { ReactNode } from "react";
import type {
  HealthCoverageResponse,
  HealthFilesQuery,
  HealthFilesResponse,
  HealthFinding,
  HealthOverviewResponse,
  HealthWorkQueueQuery,
  HealthWorkQueueResponse,
  PerformanceOpportunityDetail,
  PerformanceOpportunityPage,
  PerformanceOpportunityQuery,
  TestsReachingFile,
} from "@repowise-dev/types/health";
import type { Paginated } from "@repowise-dev/types";
import type { CodeHealthOverlay } from "./map/types";
import type {
  RefactoringOpportunity,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";

/** Subset of the findings list query the shared views need. */
export interface CodeHealthFindingsQuery {
  biomarker_type?: string;
  file_path?: string;
  min_severity?: string;
  dimension?: string;
  limit?: number;
}

export type FindingStatusValue =
  | "open"
  | "acknowledged"
  | "resolved"
  | "false_positive";

/**
 * App-injected data + navigation + slots for the shared Code Health views.
 *
 * The views own their layout, filter state, and fetch orchestration; the host
 * supplies *how* to fetch and *where* links go. Web binds this to its `/api`
 * client + `/repos/:id` routing; hosted binds it to the snapshot client + its
 * own routing. One view, two adapters — no second copy.
 *
 * `repoId`/`getOverview` are bound by the host so the views never reference an
 * app-specific data module. The methods are keyed off `cacheKey` for SWR, so
 * page-level and view-level fetches that share a key dedupe onto one request.
 */
export interface CodeHealthAdapter {
  /**
   * Seeds the views' SWR cache keys. Keep it stable per repo/snapshot and
   * identical to any key the host uses at the page level (e.g. a shared
   * overview fetch) so the requests dedupe instead of doubling up.
   */
  cacheKey: string;

  getOverview(limit: number): Promise<HealthOverviewResponse>;
  listFindings(opts?: CodeHealthFindingsQuery): Promise<HealthFinding[]>;
  getPerformanceOpportunities?(
    opts?: PerformanceOpportunityQuery,
  ): Promise<PerformanceOpportunityPage>;
  /**
   * One opportunity by its stable id. Optional: a host that has not wired it
   * renders the row it already holds and says so, rather than claiming a
   * lifecycle and analyzed commit it never read.
   */
  getPerformanceOpportunity?(
    opportunityId: string,
    opts?: { evidenceLimit?: number; evidenceOffset?: number },
  ): Promise<PerformanceOpportunityDetail>;
  getPerformanceOpportunityFindings?(
    opportunityId: string,
    opts?: { limit?: number; offset?: number },
  ): Promise<Paginated<HealthFinding>>;
  /** Fetch one exact canonical plan for the performance drawer. */
  getRefactoringPlan?(planId: string): Promise<RefactoringPlan>;
  listFiles(opts?: HealthFilesQuery): Promise<HealthFilesResponse>;
  getHealthWorkQueue?(
    opts?: HealthWorkQueueQuery,
  ): Promise<HealthWorkQueueResponse>;
  /** @deprecated Legacy adapter name; FindingsView accepts it during migration. */
  getRefactoringTargets?(
    opts?: HealthWorkQueueQuery,
  ): Promise<HealthWorkQueueResponse>;
  updateFindingStatus(
    findingId: string,
    status: FindingStatusValue,
  ): Promise<HealthFinding>;
  getCoverage(opts?: {
    file_path?: string;
    limit?: number;
    module_limit?: number;
    /** False declines the graph-inferred fallback. For cheap summary reads. */
    include_inferred?: boolean;
  }): Promise<HealthCoverageResponse>;

  /**
   * Which tests reach one file, per the dependency graph. Optional: a host that
   * has not wired it yet renders no test list rather than an error, which is
   * the same degradation the response types use for a frontend ahead of its
   * backend.
   */
  getTestsReaching?(filePath: string): Promise<TestsReachingFile>;

  /** Build an href to a file detail page. */
  fileHref(path: string): string;
  /** Build an href to a symbol detail page, or `undefined` if not linkable. */
  symbolHref?(symbolId: string): string | undefined;
  /** Exact stable-id handoff into the existing structured-plan page. */
  refactoringPlanHref?(planId: string, opportunityId: string): string;
  /**
   * The file's one composed refactoring opportunity, or null when it has none.
   * Optional: a host that cannot answer offers no link rather than a dead one.
   */
  getFileOpportunity?(filePath: string): Promise<RefactoringOpportunity | null>;
  /** Deep link into the refactoring surface for one opportunity. */
  refactoringOpportunityHref?(opportunityId: string): string;
  /**
   * Where this cause lives on the one map. Optional: a host without a galaxy
   * offers no link rather than a second map.
   */
  mapHref?(opportunityId: string, filePath: string): string;
  /** Navigate to an href (host wires this to its router). */
  navigate(href: string): void;

  /**
   * Render the app's file-detail drawer for the inspected path. Kept as a slot
   * so each app supplies its own data fetch + toast wiring; pass `null` for
   * `filePath` to render nothing.
   *
   * `lens` names the surface the file was opened from, so the drawer can lead
   * with what the reader was looking at. It is optional in both directions: a
   * host that ignores it renders exactly what it rendered before, and a caller
   * that does not know one omits it and gets the default.
   */
  renderFileDrawer(args: {
    filePath: string | null;
    onClose: () => void;
    lens?: CodeHealthOverlay;
  }): ReactNode;
}

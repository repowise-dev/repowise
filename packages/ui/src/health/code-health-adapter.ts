import type { ReactNode } from "react";
import type {
  HealthCoverageResponse,
  HealthFilesQuery,
  HealthFilesResponse,
  HealthFinding,
  HealthOverviewResponse,
  RefactoringQuery,
  RefactoringTargetsResponse,
} from "@repowise-dev/types/health";

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
  listFiles(opts?: HealthFilesQuery): Promise<HealthFilesResponse>;
  getRefactoringTargets(
    opts?: RefactoringQuery,
  ): Promise<RefactoringTargetsResponse>;
  updateFindingStatus(
    findingId: string,
    status: FindingStatusValue,
  ): Promise<HealthFinding>;
  getCoverage(opts?: {
    file_path?: string;
    limit?: number;
  }): Promise<HealthCoverageResponse>;

  /** Build an href to a file detail page. */
  fileHref(path: string): string;
  /** Build an href to a symbol detail page, or `undefined` if not linkable. */
  symbolHref?(symbolId: string): string | undefined;
  /** Navigate to an href (host wires this to its router). */
  navigate(href: string): void;

  /**
   * Render the app's file-detail drawer for the inspected path. Kept as a slot
   * so each app supplies its own data fetch + toast wiring; pass `null` for
   * `filePath` to render nothing.
   */
  renderFileDrawer(args: {
    filePath: string | null;
    onClose: () => void;
  }): ReactNode;
}

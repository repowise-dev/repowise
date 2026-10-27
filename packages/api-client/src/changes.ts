/**
 * Change endpoints: merged changes reconstructed from git.
 *
 * Types come from `@repowise-dev/types/changes`, re-exported here so a
 * consumer needs one import.
 */
import { apiGet } from "./client";
import type {
  ChangeEvidence,
  ChangeEvidenceSummary,
  ChangeListItem,
  ChangeListResponse,
  ChangeRecord,
  EvidenceItem,
} from "@repowise-dev/types/changes";
import type {
  HistoryFilters,
  PeriodHealthSummaryResponse,
} from "@repowise-dev/types/history";

export type {
  ChangeEvidence,
  ChangeEvidenceSummary,
  ChangeListItem,
  ChangeListResponse,
  ChangeRecord,
  EvidenceItem,
};

/** A page of changes, newest merged first, without file or commit lists.
 *  Each row leads with `health`, its commits' rolled-up delta. `since` and
 *  `until` read `merged_at`. */
export async function listChanges(
  repoId: string,
  opts?: HistoryFilters & {
    /** One commit category, e.g. `fix`. */
    category?: string;
    shape?: "merge" | "squash" | "commit";
    /** Substring of the title, id or a file path. */
    q?: string;
    /** Add each row's kept checklist status counts; never builds a checklist. */
    evidence?: boolean;
    /** Only changes whose kept checklist has an attention item. */
    needs_attention?: boolean;
    limit?: number;
    offset?: number;
  },
): Promise<ChangeListResponse> {
  return apiGet<ChangeListResponse>(`/api/repos/${repoId}/changes`, { ...opts });
}

/** A period headline over changes: how many made health better, worse, both
 *  or neither (improvements first), and the findings behind it. */
export async function getChangesSummary(
  repoId: string,
  filters: HistoryFilters & { category?: string } = {},
): Promise<PeriodHealthSummaryResponse> {
  return apiGet<PeriodHealthSummaryResponse>(`/api/repos/${repoId}/changes/summary`, {
    ...filters,
  });
}

/**
 * One change by id (`pr-123`), or the change that landed a commit sha.
 * `evidence` adds its checklist, built on first read (seconds) and then kept.
 */
export async function getChange(
  repoId: string,
  changeId: string,
  opts?: { evidence?: boolean },
): Promise<ChangeRecord> {
  return apiGet<ChangeRecord>(
    `/api/repos/${repoId}/changes/${encodeURIComponent(changeId)}`,
    opts,
  );
}

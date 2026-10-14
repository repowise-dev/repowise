/**
 * Change endpoints: merged changes reconstructed from git.
 *
 * Types come from `@repowise-dev/types/changes`, re-exported here so a
 * consumer needs one import.
 */
import { apiGet } from "./client";
import type {
  ChangeListResponse,
  ChangeRecord,
  ChangeSummary,
} from "@repowise-dev/types/changes";

export type { ChangeListResponse, ChangeRecord, ChangeSummary };

/** A page of changes, newest merged first, without file or commit lists. */
export async function listChanges(
  repoId: string,
  opts?: {
    /** One commit category, e.g. `fix`. */
    category?: string;
    shape?: "merge" | "squash" | "commit";
    /** Substring of the title, id or a file path. */
    q?: string;
    limit?: number;
    offset?: number;
  },
): Promise<ChangeListResponse> {
  return apiGet<ChangeListResponse>(`/api/repos/${repoId}/changes`, opts);
}

/** One change by id (`pr-123`), or the change that landed a commit sha. */
export async function getChange(
  repoId: string,
  changeId: string,
): Promise<ChangeRecord> {
  return apiGet<ChangeRecord>(
    `/api/repos/${repoId}/changes/${encodeURIComponent(changeId)}`,
  );
}

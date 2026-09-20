/**
 * Documentation-drift API client. The response types live in the shared
 * `@repowise-dev/types/doc-drift` contract; this module re-exports them and
 * keeps only the fetch functions.
 */
import type {
  DocDriftReferencesResponse,
  DocDriftResponse,
} from "@repowise-dev/types/doc-drift";
import { apiGet } from "./client";

export type {
  DocDriftDocumentDrift,
  DocDriftFinding,
  DocDriftKind,
  DocDriftReference,
  DocDriftReferencesResponse,
  DocDriftResponse,
  DocDriftSummary,
  DocDriftUnavailable,
} from "@repowise-dev/types/doc-drift";

/**
 * Documents whose assertions the repository no longer satisfies.
 *
 * The response carries its own summary; do not recompute one from `findings`,
 * which is capped by `limit` while the summary counts the whole query.
 */
export async function getDocDrift(
  repoId: string,
  opts?: {
    min_confidence?: number;
    kind?: string;
    document?: string;
    limit?: number;
  },
): Promise<DocDriftResponse> {
  return apiGet<DocDriftResponse>(`/api/repos/${repoId}/doc-drift`, opts);
}

/**
 * Which documents name `target`, and which of them carry drift.
 *
 * A reference says a document names a file the repository still has. It does
 * not say the document describes it.
 */
export async function getDocDriftReferences(
  repoId: string,
  target: string,
  opts?: { limit?: number },
): Promise<DocDriftReferencesResponse> {
  return apiGet<DocDriftReferencesResponse>(
    `/api/repos/${repoId}/doc-drift/references`,
    { target, ...opts },
  );
}

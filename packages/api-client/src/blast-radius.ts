import { apiGet, apiPost } from "./client";
import type {
  BlastRadiusRequest,
  BlastRadiusResponse,
  ChangeBlastRadiusRequest,
  ChangeBlastRadiusResponse,
  ChangeCandidatesResponse,
} from "@repowise-dev/types/blast-radius";

export type {
  DirectRiskEntry,
  TransitiveEntry,
  CochangeWarning,
  ReviewerEntry,
  BlastRadiusResponse,
  BlastRadiusRequest,
  AffectedSignal,
  ChangeCandidate,
  ChangeCandidatesResponse,
  ChangeBlastRadiusRequest,
  ChangeBlastRadiusResponse,
} from "@repowise-dev/types/blast-radius";

export async function analyzeBlastRadius(
  repoId: string,
  body: BlastRadiusRequest,
): Promise<BlastRadiusResponse> {
  return apiPost<BlastRadiusResponse>(
    `/api/repos/${repoId}/blast-radius`,
    body,
  );
}

/** Changes to pick: uncommitted work, branches ahead of the default branch, recent commits. */
export async function listBlastRadiusChanges(repoId: string): Promise<ChangeCandidatesResponse> {
  return apiGet<ChangeCandidatesResponse>(`/api/repos/${repoId}/blast-radius/changes`);
}

/** Blast radius of one listed change, with the reach edges and per-file signals a drawing needs. */
export async function analyzeChangeBlastRadius(
  repoId: string,
  body: ChangeBlastRadiusRequest,
): Promise<ChangeBlastRadiusResponse> {
  return apiPost<ChangeBlastRadiusResponse>(`/api/repos/${repoId}/blast-radius/change`, body);
}

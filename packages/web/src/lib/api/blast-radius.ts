import { apiPost } from "./client";
import type {
  BlastRadiusRequest,
  BlastRadiusResponse,
} from "@repowise-dev/types/blast-radius";

export type {
  DirectRiskEntry,
  TransitiveEntry,
  CochangeWarning,
  ReviewerEntry,
  BlastRadiusResponse,
  BlastRadiusRequest,
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

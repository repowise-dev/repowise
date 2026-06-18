import { apiGet, apiPost, apiPatch } from "./client";
import type {
  DecisionCreate,
  DecisionEvidence,
  DecisionEvidenceResponse,
  DecisionGraph,
  DecisionLineageEntry,
  DecisionLineageResponse,
  DecisionRecordResponse,
  DecisionStatusUpdate,
} from "./types";

export async function listDecisions(
  repoId: string,
  opts?: {
    status?: string;
    source?: string;
    tag?: string;
    module?: string;
    include_proposed?: boolean;
    limit?: number;
  },
): Promise<DecisionRecordResponse[]> {
  return apiGet<DecisionRecordResponse[]>(`/api/repos/${repoId}/decisions`, opts);
}

export async function getDecision(
  repoId: string,
  decisionId: string,
): Promise<DecisionRecordResponse> {
  return apiGet<DecisionRecordResponse>(
    `/api/repos/${repoId}/decisions/${decisionId}`,
  );
}

export async function createDecision(
  repoId: string,
  data: DecisionCreate,
): Promise<DecisionRecordResponse> {
  return apiPost<DecisionRecordResponse>(`/api/repos/${repoId}/decisions`, data);
}

export async function patchDecision(
  repoId: string,
  decisionId: string,
  data: DecisionStatusUpdate,
): Promise<DecisionRecordResponse> {
  return apiPatch<DecisionRecordResponse>(
    `/api/repos/${repoId}/decisions/${decisionId}`,
    data,
  );
}

export async function getDecisionEvidence(
  repoId: string,
  decisionId: string,
): Promise<DecisionEvidence[]> {
  const res = await apiGet<DecisionEvidenceResponse>(
    `/api/repos/${repoId}/decisions/${decisionId}/evidence`,
  );
  return res.evidence;
}

export async function getDecisionLineage(
  repoId: string,
  decisionId: string,
): Promise<DecisionLineageEntry[]> {
  const res = await apiGet<DecisionLineageResponse>(
    `/api/repos/${repoId}/decisions/${decisionId}/lineage`,
  );
  return res.lineage;
}

export async function getDecisionGraph(repoId: string): Promise<DecisionGraph> {
  return apiGet<DecisionGraph>(`/api/repos/${repoId}/decisions/graph`);
}

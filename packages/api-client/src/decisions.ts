import { apiGet, apiPost, apiPatch } from "./client";
import type {
  DecisionCreate,
  DecisionEvidence,
  DecisionEvidenceResponse,
  DecisionCounts,
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
    offset?: number;
    /**
     * `priority` (server default) leads with confirmed rules, then the
     * highest-confidence proposals. `recent` is newest-first.
     */
    sort?: "priority" | "recent";
  },
): Promise<DecisionRecordResponse[]> {
  return apiGet<DecisionRecordResponse[]>(`/api/repos/${repoId}/decisions`, opts);
}

/**
 * Counts by status. A grouped COUNT, so the total is measured rather than
 * inferred from however many rows a page happened to fetch.
 */
export async function getDecisionCounts(
  repoId: string,
  opts?: {
    source?: string;
    tag?: string;
    module?: string;
    include_proposed?: boolean;
  },
): Promise<DecisionCounts> {
  return apiGet<DecisionCounts>(`/api/repos/${repoId}/decisions/counts`, opts);
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

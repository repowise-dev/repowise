/**
 * Runtime evidence: run status and freshness, stored runs, ranked measured
 * functions per workload, and the measured view of one function or file.
 */
import type {
  EvidenceRunsResponse,
  EvidenceStatusResponse,
  MeasuredFunctionsResponse,
  MeasuredResponse,
} from "@repowise-dev/types/runtime-evidence";
import { apiGet } from "./client";

export type {
  EvidenceRunSummary,
  EvidenceRunsResponse,
  EvidenceStatusResponse,
  EvidenceStatusToken,
  EvidenceUnits,
  MeasuredFunction,
  MeasuredFunctionsResponse,
  MeasuredGroup,
  MeasuredOwner,
  MeasuredResponse,
  MeasuredRow,
  MeasuredRun,
} from "@repowise-dev/types/runtime-evidence";

/** The newest run of every workload, and whether anything is ranked. */
export async function getEvidenceStatus(repoId: string): Promise<EvidenceStatusResponse> {
  return apiGet<EvidenceStatusResponse>(`/api/repos/${repoId}/evidence/status`);
}

/** Every stored run, newest first. */
export async function listEvidenceRuns(repoId: string): Promise<EvidenceRunsResponse> {
  return apiGet<EvidenceRunsResponse>(`/api/repos/${repoId}/evidence/runs`);
}

/** Each workload's newest run with its ranked functions; `runId` keeps one run. */
export async function getMeasured(
  repoId: string,
  opts: {
    runId?: string;
    /** Rows per run, 0-100; the server defaults to 10. */
    limit?: number;
  } = {},
): Promise<MeasuredResponse> {
  return apiGet<MeasuredResponse>(`/api/repos/${repoId}/evidence/measured`, {
    run_id: opts.runId,
    limit: opts.limit,
  });
}

/** The measured view of one function or of every function in a file. */
export async function getMeasuredFunctions(
  repoId: string,
  target: { symbolId: string; file?: never } | { file: string; symbolId?: never },
): Promise<MeasuredFunctionsResponse> {
  return apiGet<MeasuredFunctionsResponse>(`/api/repos/${repoId}/evidence/functions`, {
    symbol_id: target.symbolId,
    file: target.file,
  });
}

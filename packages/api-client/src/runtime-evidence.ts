/**
 * Runtime evidence: run status and freshness, stored runs, ranked measured
 * functions per workload, and the measured view of one function or file.
 */
import type {
  EvidenceRunsResponse,
  EvidenceStatusResponse,
  MeasuredFunctionsResponse,
  MeasuredResponse,
  SuiteCostResponse,
} from "@repowise-dev/types/runtime-evidence";
import { apiRequest, withQuery, type ApiRequest } from "./client";

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
  SlowTest,
  SuiteCostResponse,
} from "@repowise-dev/types/runtime-evidence";

/**
 * The runtime-evidence requests over *request*, under *base*: the evidence
 * root for a repository (`/api/repos/{id}/evidence` here). A host with its own
 * fetch layer and routes builds its client from this, so paths, query names
 * and types stay in one place.
 */
export function createRuntimeEvidenceApi(request: ApiRequest, base: (repoId: string) => string) {
  return {
    /** The newest run of every workload, and whether anything is ranked. */
    getEvidenceStatus: (repoId: string) =>
      request<EvidenceStatusResponse>(`${base(repoId)}/status`),

    /** Every stored run, newest first. */
    listEvidenceRuns: (repoId: string) => request<EvidenceRunsResponse>(`${base(repoId)}/runs`),

    /** Each workload's newest run with its ranked functions; `runId` keeps one run. */
    getMeasured: (
      repoId: string,
      opts: {
        runId?: string;
        /** Rows per run, 0-100; the server defaults to 10. */
        limit?: number;
      } = {},
    ) =>
      request<MeasuredResponse>(
        withQuery(`${base(repoId)}/measured`, { run_id: opts.runId, limit: opts.limit }),
      ),

    /** The measured view of one function or of every function in a file. */
    getMeasuredFunctions: (
      repoId: string,
      target: { symbolId: string; file?: never } | { file: string; symbolId?: never },
    ) =>
      request<MeasuredFunctionsResponse>(
        withQuery(`${base(repoId)}/functions`, { symbol_id: target.symbolId, file: target.file }),
      ),

    /** The slowest timed tests and the test time per production file, from stored JUnit timings. */
    getEvidenceTests: (repoId: string, opts: { limit?: number } = {}) =>
      request<SuiteCostResponse>(withQuery(`${base(repoId)}/tests`, { limit: opts.limit })),
  };
}

export const {
  getEvidenceStatus,
  listEvidenceRuns,
  getMeasured,
  getMeasuredFunctions,
  getEvidenceTests,
} = createRuntimeEvidenceApi(apiRequest, (repoId) => `/api/repos/${repoId}/evidence`);

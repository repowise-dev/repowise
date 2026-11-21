/**
 * Runtime evidence: what stored profile runs measured, and on what basis.
 *
 * Generated from the server's response models
 * (`packages/server/src/repowise/server/schemas/runtime_evidence.py`) for
 * `GET /api/repos/{id}/evidence/status`, `/evidence/runs`,
 * `/evidence/measured` and `/evidence/functions`. Every measured value names
 * its basis: workload class and name, commit, age and samples. Shares are
 * fractions of the run (0-1) with an interval where the run counted samples;
 * `repo_self_share` is how much of the run repo code itself explains. No row
 * means unknown, never zero.
 */
export type {
  EvidenceRunSummary,
  EvidenceRunsResponse,
  EvidenceStatusResponse,
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
} from "./generated/http.js";

import type { EvidenceStatusResponse } from "./generated/http.js";

/** Whether anything is ranked, or why not. */
export type EvidenceStatusToken = EvidenceStatusResponse["status"];

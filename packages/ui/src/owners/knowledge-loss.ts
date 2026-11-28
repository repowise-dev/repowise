/**
 * Knowledge loss: people who have gone quiet while still owning code that
 * changes. Each one is read as a single-person off-boarding over the files
 * changed in the last 90 days, since in effect they already left: what code
 * still moving lost its majority author, and who stays to take it.
 *
 * Pure. The quiet list is capped server side (25), so this is at most 25
 * passes over the matrix.
 */

import type { KnowledgeMatrix, QuietOwner } from "@repowise-dev/types/owners";
import { simulateOffboarding, type OffboardingResult } from "./offboarding";

export interface QuietOwnerExposure {
  owner: QuietOwner;
  result: OffboardingResult;
}

/** Every quiet owner gone at once, over changing code: the section-level handover. */
export function quietOwnersTogether(
  matrix: KnowledgeMatrix,
  owners: readonly QuietOwner[],
  threshold?: number,
): OffboardingResult {
  return simulateOffboarding(
    matrix,
    owners.map((o) => o.key),
    threshold,
    { changedOnly: true },
  );
}

/** Quiet owners with their exposure, the most severe files first. */
export function quietOwnerExposure(
  matrix: KnowledgeMatrix,
  owners: readonly QuietOwner[],
  threshold?: number,
): QuietOwnerExposure[] {
  return owners
    .map((owner) => ({
      owner,
      result: simulateOffboarding(matrix, [owner.key], threshold, { changedOnly: true }),
    }))
    .sort(
      (a, b) =>
        b.result.totals.severeFiles - a.result.totals.severeFiles ||
        b.result.totals.atRiskFiles - a.result.totals.atRiskFiles ||
        b.owner.changing_files_owned - a.owner.changing_files_owned,
    );
}

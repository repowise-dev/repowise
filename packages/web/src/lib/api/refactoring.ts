/**
 * REST client for the refactoring endpoints.
 * Backend: packages/server/src/repowise/server/routers/refactoring.py
 */

import { apiGet } from "./client";
import type { RefactoringPlan, RefactoringTargets } from "@repowise-dev/ui/refactoring";

export interface RefactoringTargetsParams {
  refactoringType?: string;
  minConfidence?: string;
}

export async function getRefactoringTargets(
  repoId: string,
  params: RefactoringTargetsParams = {},
): Promise<RefactoringTargets> {
  return apiGet<RefactoringTargets>(`/api/repos/${repoId}/refactoring/targets`, {
    refactoring_type: params.refactoringType,
    min_confidence: params.minConfidence,
  });
}

export async function getRefactoringPlan(
  repoId: string,
  suggestionId: string,
): Promise<RefactoringPlan> {
  return apiGet<RefactoringPlan>(`/api/repos/${repoId}/refactoring/${suggestionId}`);
}

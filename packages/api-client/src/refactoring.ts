/**
 * REST client for the refactoring endpoints.
 * Backend: packages/server/src/repowise/server/routers/refactoring.py
 */

import { apiGet, apiPost, apiPut } from "./client";
import type { GeneratedCode, RefactoringPlan, RefactoringTargets } from "@repowise-dev/ui/refactoring";

export interface RefactoringSettings {
  enabled: boolean;
  provider: string | null;
  model: string | null;
}

export interface GenerateCodeOverrides {
  provider?: string;
  model?: string;
}

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

/** Opt-in: generate the refactored code + a diff for one plan (Phase-5 endpoint). */
export async function generateRefactoringCode(
  repoId: string,
  suggestionId: string,
  overrides: GenerateCodeOverrides = {},
): Promise<GeneratedCode> {
  return apiPost<GeneratedCode>(
    `/api/repos/${repoId}/refactoring/${suggestionId}/generate-code`,
    overrides,
  );
}

export async function getRefactoringSettings(repoId: string): Promise<RefactoringSettings> {
  return apiGet<RefactoringSettings>(`/api/repos/${repoId}/refactoring/settings`);
}

export async function updateRefactoringSettings(
  repoId: string,
  settings: RefactoringSettings,
): Promise<RefactoringSettings> {
  return apiPut<RefactoringSettings>(`/api/repos/${repoId}/refactoring/settings`, settings);
}

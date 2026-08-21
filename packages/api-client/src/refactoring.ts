/**
 * REST client for the refactoring endpoints.
 * Backend: packages/server/src/repowise/server/routers/refactoring.py
 */

import { apiGet, apiPost, apiPut } from "./client";
import type {
  GeneratedCode,
  RefactoringPlanPage,
  RefactoringPlan,
  RefactoringTargets,
} from "@repowise-dev/types/refactoring";

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
  /** Repo-relative path; narrows plans to one file (summary stays global). */
  filePath?: string;
  view?: "canonical" | "file_spread";
}

export type RefactoringSort = "canonical" | "health" | "effort" | "blast" | "file";

export interface RefactoringPageParams extends RefactoringTargetsParams {
  search?: string;
  confidence?: string;
  effort?: string;
  sort?: RefactoringSort;
  limit?: number;
  offset?: number;
}

export async function getRefactoringTargets(
  repoId: string,
  params: RefactoringTargetsParams = {},
): Promise<RefactoringTargets> {
  return apiGet<RefactoringTargets>(`/api/repos/${repoId}/refactoring/targets`, {
    refactoring_type: params.refactoringType,
    min_confidence: params.minConfidence,
    file_path: params.filePath,
    view: params.view,
  });
}

/** Bounded server-filtered list for product surfaces. */
export async function getRefactoringPlansPage(
  repoId: string,
  params: RefactoringPageParams = {},
): Promise<RefactoringPlanPage> {
  return apiGet<RefactoringPlanPage>(`/api/repos/${repoId}/refactoring/targets/page`, {
    refactoring_type: params.refactoringType,
    min_confidence: params.minConfidence,
    file_path: params.filePath,
    view: params.view,
    search: params.search,
    confidence: params.confidence,
    effort: params.effort,
    sort: params.sort,
    limit: params.limit,
    offset: params.offset,
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

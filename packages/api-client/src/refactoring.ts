/**
 * REST client for the refactoring endpoints.
 * Backend: packages/server/src/repowise/server/routers/refactoring.py
 */

import { apiGet, apiPatch, apiPost, apiPut } from "./client";
import type {
  GeneratedCode,
  RefactoringOpportunityDetail,
  RefactoringOpportunityPage,
  RefactoringOpportunityStatusUpdate,
  RefactoringOrder,
  RefactoringPlanPage,
  RefactoringPlan,
  RefactoringPlanStatusUpdate,
  RefactoringSummaryResponse,
  RefactoringTargets,
  RefactoringTriageStatus,
  RefactoringView,
} from "@repowise-dev/types/refactoring";

export type {
  RefactoringOpportunityDetail,
  RefactoringOpportunityPage,
  RefactoringSummaryResponse,
  RefactoringTriageStatus,
};

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

// ---------------------------------------------------------------------------
// Composed opportunities (R4's read model). The plan endpoints above stay: a
// plan is still addressable by id, and the drawer's per-step detail reads them.
// ---------------------------------------------------------------------------

export interface RefactoringOpportunityParams {
  /** Lead refactoring type, not the type of any member step. */
  refactoringType?: string;
  /** Triage state. Defaults to `open` server-side. */
  status?: RefactoringTriageStatus;
  confidence?: string;
  effort?: string;
  /** One repo-relative path. This is how a file surface asks for its own work. */
  filePath?: string;
  /** Substring of the file path, for the board's search box. */
  search?: string;
  /** Only opportunities carrying at least one mechanical step. */
  mechanical?: boolean;
  view?: RefactoringView;
  order?: RefactoringOrder;
  /** Steps inlined per row. 0 for a list that renders counts and opens a drawer. */
  stepPreview?: number;
  limit?: number;
  offset?: number;
}

export async function getRefactoringOpportunities(
  repoId: string,
  params: RefactoringOpportunityParams = {},
): Promise<RefactoringOpportunityPage> {
  return apiGet<RefactoringOpportunityPage>(
    `/api/repos/${repoId}/refactoring/opportunities`,
    {
      refactoring_type: params.refactoringType,
      status: params.status,
      confidence: params.confidence,
      effort: params.effort,
      file_path: params.filePath,
      search: params.search,
      // Only sent when true: the server defaults it to false, and sending
      // `false` would turn a shared default into a caller's assertion.
      mechanical: params.mechanical ? true : undefined,
      view: params.view,
      order: params.order,
      step_preview: params.stepPreview,
      limit: params.limit,
      offset: params.offset,
    },
  );
}

export async function getRefactoringOpportunity(
  repoId: string,
  opportunityId: string,
  opts: {
    stepLimit?: number;
    stepOffset?: number;
    evidenceLimit?: number;
    evidenceOffset?: number;
  } = {},
): Promise<RefactoringOpportunityDetail> {
  return apiGet<RefactoringOpportunityDetail>(
    `/api/repos/${repoId}/refactoring/opportunities/${encodeURIComponent(opportunityId)}`,
    {
      step_limit: opts.stepLimit,
      step_offset: opts.stepOffset,
      evidence_limit: opts.evidenceLimit,
      evidence_offset: opts.evidenceOffset,
    },
  );
}

/** The repository rollup and its one lead, by primary key. */
export async function getRefactoringOpportunitySummary(
  repoId: string,
): Promise<RefactoringSummaryResponse> {
  return apiGet<RefactoringSummaryResponse>(`/api/repos/${repoId}/refactoring/summary`);
}

/**
 * Record a decision about one opportunity.
 *
 * One request, not one per step: the server applies the transition to every
 * member plan through the same owner the plan route uses, then rolls the
 * opportunity's own state up from them.
 */
export async function updateRefactoringOpportunityStatus(
  repoId: string,
  opportunityId: string,
  status: RefactoringTriageStatus,
): Promise<RefactoringOpportunityStatusUpdate> {
  return apiPatch<RefactoringOpportunityStatusUpdate>(
    `/api/repos/${repoId}/refactoring/opportunities/${encodeURIComponent(opportunityId)}/status`,
    { status },
  );
}

/** Record a decision about one plan (one step of an opportunity). */
export async function updateRefactoringPlanStatus(
  repoId: string,
  suggestionId: string,
  status: RefactoringTriageStatus,
): Promise<RefactoringPlanStatusUpdate> {
  return apiPatch<RefactoringPlanStatusUpdate>(
    `/api/repos/${repoId}/refactoring/${encodeURIComponent(suggestionId)}/status`,
    { status },
  );
}

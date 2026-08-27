import type { ExternalSystemsSummaryScope } from "@repowise-dev/types/external-systems";

export const PACKAGE_SUMMARY_LIMIT = 400;

export function packageSummaryRequest(repoId: string, scope: ExternalSystemsSummaryScope) {
  return {
    path: `/api/repos/${repoId}/external-systems/summary`,
    params: { scope, limit: PACKAGE_SUMMARY_LIMIT },
  } as const;
}

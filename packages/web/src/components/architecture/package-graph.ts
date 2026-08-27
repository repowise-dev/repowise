import type { NodeSearchResult } from "../../lib/api/types";
import type { ExternalSystemsSummaryScope } from "@repowise-dev/types/external-systems";

export const PACKAGE_SUMMARY_LIMIT = 400;

export function packageSummaryRequest(repoId: string, scope: ExternalSystemsSummaryScope) {
  return {
    path: `/api/repos/${repoId}/external-systems/summary`,
    params: { scope, limit: PACKAGE_SUMMARY_LIMIT },
  } as const;
}

function externalCandidateScore(candidate: NodeSearchResult, packageName: string): number {
  const nodeId = candidate.node_id.toLowerCase();
  const expected = `external:${packageName}`.toLowerCase();
  if (nodeId === expected) return 3;
  if (nodeId.startsWith(`${expected}/`) || nodeId.startsWith(`${expected}:`)) return 2;
  return 0;
}

export function chooseExternalPackageNode(
  candidates: NodeSearchResult[],
  packageName: string,
): string | null {
  return candidates
    .filter((candidate) => candidate.node_id.startsWith("external:"))
    .map((candidate) => ({ candidate, score: externalCandidateScore(candidate, packageName) }))
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score || a.candidate.node_id.localeCompare(b.candidate.node_id))[0]
    ?.candidate.node_id ?? null;
}

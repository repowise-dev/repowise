/**
 * Change-coupling API client. The response types live in the shared
 * `@repowise-dev/types/coupling` contract; this module re-exports them and
 * keeps only the fetch function.
 */
import type { CouplingGraphResponse } from "@repowise-dev/types/coupling";
import { apiGet } from "./client";

export type {
  CouplingEdge,
  CouplingGraphResponse,
  CouplingNode,
} from "@repowise-dev/types/coupling";

export async function getCoupling(
  repoId: string,
  opts?: { limit?: number },
): Promise<CouplingGraphResponse> {
  return apiGet<CouplingGraphResponse>(`/api/repos/${repoId}/coupling`, opts);
}

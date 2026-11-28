import type { SecurityFinding, SecuritySummary } from "@repowise-dev/types/security";
import { apiGet } from "./client";

// One definition of the wire shapes: the types package's, so a field the
// server adds (path class, class counts) reaches every typed consumer.
export type { SecurityFinding, SecurityPathClass, SecuritySummary } from "@repowise-dev/types/security";

export async function getSecuritySummary(repoId: string): Promise<SecuritySummary> {
  return apiGet<SecuritySummary>(`/api/repos/${repoId}/security/summary`);
}

export async function listSecurityFindings(
  repoId: string,
  opts: {
    file_path?: string;
    severity?: string;
    limit?: number;
  } = {},
): Promise<SecurityFinding[]> {
  return apiGet<SecurityFinding[]>(`/api/repos/${repoId}/security`, {
    file_path: opts.file_path,
    severity: opts.severity,
    limit: opts.limit,
  });
}

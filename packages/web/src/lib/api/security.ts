import { apiGet } from "./client";

export interface SecurityFinding {
  id: number;
  file_path: string;
  kind: string;
  severity: "high" | "med" | "low" | string;
  snippet: string | null;
  detected_at: string;
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

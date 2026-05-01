/**
 * Canonical security finding types.
 *
 * Mirrors the engine's `security_findings` table (per-snapshot persisted
 * results from the `security_scan` analyser) and the consumer-side shape
 * already used by the OSS web `listSecurityFindings` API client.
 */

export type SecuritySeverity = "high" | "med" | "low" | string;

export interface SecurityFinding {
  id: number;
  file_path: string;
  kind: string;
  severity: SecuritySeverity;
  snippet: string | null;
  detected_at: string;
}

export interface SecurityFindingList {
  total: number;
  findings: SecurityFinding[];
}

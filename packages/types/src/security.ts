/**
 * Canonical security finding types.
 *
 * Mirrors the engine's `security_findings` table (per-snapshot persisted
 * results from the `security_scan` analyser) and the consumer-side shape
 * already used by the OSS web `listSecurityFindings` API client.
 */

export type SecuritySeverity = "high" | "med" | "low" | (string & {});

export interface SecurityFinding {
  id: number;
  file_path: string;
  kind: string;
  severity: SecuritySeverity;
  snippet: string | null;
  detected_at: string;
  /**
   * Line the finding sits on, checked against the live tree before it is
   * served. `null` means the snippet is no longer in the file, so no line can
   * honestly be given — render the path alone rather than a stale number.
   */
  line_number: number | null;
  /**
   * False when the line could not be confirmed (file unreadable, or the
   * snippet recurs). Surfaces must mark it rather than presenting a guess as
   * fact.
   */
  line_verified: boolean;
  /** When the introducing commit landed; only set for history findings. */
  commit_at: string | null;
}

export interface SecurityFindingList {
  total: number;
  findings: SecurityFinding[];
}

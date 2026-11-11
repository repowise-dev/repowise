/**
 * Canonical security finding types.
 *
 * Mirrors the engine's `security_findings` table (per-snapshot persisted
 * results from the `security_scan` analyser) and the consumer-side shape
 * already used by the OSS web `listSecurityFindings` API client.
 */

export type SecuritySeverity = "high" | "med" | "low" | (string & {});

/** Shipped source, tests, or docs, as the server classifies the file. */
export type SecurityPathClass = "source" | "test" | "docs";

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
  /** Shipped source, tests, or docs. Absent on older servers. */
  path_class?: SecurityPathClass;
}

/** Run metadata for the pattern scan, served beside the findings list. */
export interface SecuritySummary {
  /**
   * ISO 8601 UTC time the scan last ran. `null` or absent (older servers)
   * means never ran or unknown, which is not an all-clear.
   */
  scanned_at?: string | null;
  /** Every stored finding. Absent on older servers, with the two below. */
  total?: number;
  /** Findings by where they sit: shipped source, tests, docs. */
  class_counts?: Partial<Record<SecurityPathClass, number>>;
  /** High-severity findings in shipped source. */
  source_high?: number;
}

export interface SecurityFindingList {
  total: number;
  findings: SecurityFinding[];
}

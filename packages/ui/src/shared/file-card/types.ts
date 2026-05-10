/**
 * Universal file overview shape — render whatever sections have data.
 * Hosts pass undefined to hide a section. Designed to be assembled from
 * data already on the page (no extra fetches required) but also fine to
 * source from dedicated endpoints.
 */
export interface FileCardData {
  file_path: string;
  language?: string;
  summary?: string;

  /** Git / churn / ownership signals. */
  git?: {
    churn_percentile?: number;
    commit_count_90d?: number;
    lines_added_90d?: number;
    lines_deleted_90d?: number;
    bus_factor?: number;
    primary_owner?: string | null;
    is_hotspot?: boolean;
    temporal_hotspot_score?: number | null;
  };

  /** Documentation status. When `has_doc` is false the section becomes a CTA. */
  docs?: {
    has_doc: boolean;
    doc_url?: string;
    freshness_pct?: number; // 0–100
    last_updated?: string;
  };

  /** Symbols summary. */
  symbols?: {
    total: number;
    top?: Array<{ id: string; name: string; kind?: string; importance?: number }>;
  };

  /** Dead-code findings affecting this file. */
  deadCode?: {
    findings_count: number;
    reclaimable_lines?: number;
  };

  /** Decisions affecting this file (from affected_modules_json). */
  decisions?: {
    count: number;
    titles?: string[];
  };

  /** Security findings on this file. */
  security?: {
    findings_count: number;
    critical_count?: number;
  };
}

export interface FileCardLinks {
  graph?: string;
  docs?: string;
  blastRadius?: string;
  symbols?: string;
  decisions?: string;
  security?: string;
  deadCode?: string;
}

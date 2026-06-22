/**
 * Overview-summary contract — the one-call payload behind the repo
 * Overview page (`GET /api/repos/{id}/overview-summary`). Mirrors the
 * server's `routers/overview.py` response shape.
 */

import type { Hotspot } from "./git.js";

export interface OverviewRepoMeta {
  id: string;
  name: string;
  local_path: string;
  default_branch: string;
  head_commit: string | null;
  updated_at: string | null;
}

export interface OverviewStatDeltas {
  average_health: number | null;
  hotspot_health: number | null;
  file_count: number | null;
}

export interface OverviewStats {
  file_count: number;
  symbol_count: number;
  entry_point_count: number;
  doc_coverage_pct: number;
  freshness_score: number;
  dead_export_count: number;
  hotspot_count: number;
  silo_count: number;
  module_count: number;
  deltas: OverviewStatDeltas;
}

export interface OverviewHealthHistoryPoint {
  taken_at: string | null;
  average_health: number;
  hotspot_health: number;
}

export interface OverviewHealth {
  average_health: number | null;
  hotspot_health: number | null;
  worst_performer_path: string | null;
  worst_performer_score: number | null;
  open_findings: number;
  /** Co-equal maintainability pillar headline (NLOC-weighted). `null` when no
   *  file carries a maintainability score yet. */
  maintainability_average?: number | null;
  /** Co-equal performance pillar headline: static performance RISK (I/O-in-loop
   *  / N+1). `null` when not yet measured. */
  performance_average?: number | null;
  /** Open findings homing under the performance pillar — the actionable count. */
  performance_findings?: number;
  /** Lowest-scoring file by performance risk, surfaced only when score < 10. */
  worst_performance_path?: string | null;
  worst_performance_score?: number | null;
  severity_breakdown: Record<string, number>;
  last_indexed_at: string | null;
  snapshot_count: number;
  history: OverviewHealthHistoryPoint[];
}

export interface OverviewLanguage {
  language: string;
  file_count: number;
}

export type OverviewAttentionType =
  | "stale_decision"
  | "proposed_decision"
  | "ungoverned_hotspot"
  | "knowledge_silo"
  | "dead_code";

export interface OverviewAttentionItem {
  id: string;
  type: OverviewAttentionType;
  title: string;
  description: string;
  severity: "high" | "medium" | "low";
  /** Decision id, file path, … — what the item points at. */
  target_id: string;
}

export interface OverviewOnboardingTarget {
  path: string;
  pagerank: number;
  doc_words: number;
}

export interface OverviewDecisionSlim {
  id: string;
  title: string;
  status: string;
  source: string | null;
  created_at: string | null;
  staleness_score: number;
}

export interface OverviewSavings {
  available: boolean;
  saved_tokens?: number;
  mcp_tokens?: number;
  total_saved_tokens?: number;
  estimated_usd_saved?: number;
  pricing_model?: string;
}

export interface OverviewSyncStatus {
  last_sync_at: string | null;
  last_resync_at: string | null;
  last_sync_model: string | null;
  active_job_id: string | null;
  page_count: number;
}

export interface OverviewSummaryResponse {
  repo: OverviewRepoMeta;
  stats: OverviewStats;
  health: OverviewHealth;
  languages: OverviewLanguage[];
  attention: OverviewAttentionItem[];
  onboarding_targets: OverviewOnboardingTarget[];
  top_hotspots: Hotspot[];
  recent_decisions: OverviewDecisionSlim[];
  savings: OverviewSavings;
  sync: OverviewSyncStatus;
}

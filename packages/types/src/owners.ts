/**
 * Owner / contributor profile types — engineering-leader view of who does
 * what in the codebase. Mirrors the Pydantic models served by
 * `/api/repos/{id}/owners` and `/api/repos/{id}/owners/{key}`.
 */

/** Who an identity is: a person, other automation, or a coding agent. */
export type IdentityKind = "human" | "bot" | "agent";

export interface OwnerListEntry {
  key: string;
  name: string;
  email: string | null;
  kind: IdentityKind;
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  /** Distinct commits in the 90 days before HEAD; null when the indexed commits do not cover the window. */
  commit_count_90d: number | null;
  /** A floor of that count when the indexed commits stop inside the window; null otherwise. */
  commit_count_90d_at_least?: number | null;
  last_commit_at: string | null;
  /** Owned files whose bus factor is exactly 1 (0 means no indexed history). */
  bus_factor_risk_files: number;
}

export interface OwnerModuleRollup {
  module_path: string;
  file_count: number;
  hotspot_count: number;
  /** Share of this module's files owned by this person (0–1). */
  dominant_pct: number;
}

export interface OwnerFileEntry {
  file_path: string;
  commit_count_90d: number;
  /** 0–100 percentile rank. */
  churn_percentile: number;
  bus_factor: number;
  is_hotspot: boolean;
  last_commit_at: string | null;
  primary_owner_commit_pct: number | null;
}

export interface OwnerCoAuthor {
  name: string;
  email: string | null;
  shared_files: number;
  /** Shared-files / total-files-touched (0–1). */
  co_change_strength: number;
}

/** Every display name and canonical email folded into one person. */
export interface OwnerAliases {
  names: string[];
  emails: string[];
}

export interface OwnerProfile {
  key: string;
  name: string;
  email: string | null;
  kind: IdentityKind;
  aliases: OwnerAliases;
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  /** Distinct commits in the 90 days before HEAD; null when the indexed commits do not cover the window. */
  commit_count_90d: number | null;
  /** A floor of that count when the indexed commits stop inside the window; null otherwise. */
  commit_count_90d_at_least?: number | null;
  last_commit_at: string | null;
  first_commit_at: string | null;
  /** Owned files whose bus factor is exactly 1 (0 means no indexed history). */
  bus_factor_risk_files: number;
  /** Estimated, not exact — see Pydantic doc. */
  lines_added_90d_est: number;
  lines_deleted_90d_est: number;
  modules: OwnerModuleRollup[];
  top_files: OwnerFileEntry[];
  /** Uncapped count behind the top_files slice (for "+N more"). */
  files_touched_total?: number;
  co_authors: OwnerCoAuthor[];
  /** Uncapped count behind the co_authors slice (for "+N more"). */
  co_authors_total?: number;
  commit_categories: Record<string, number>;
}

// ---------------------------------------------------------------------------
// /owners/summary: repository knowledge risk, people only
// ---------------------------------------------------------------------------

export interface OwnerRef {
  key: string;
  name: string;
}

export interface ConcentrationOwner extends OwnerRef {
  files_owned: number;
  /** Share of person-owned files (0-1). */
  share: number;
}

export interface OwnershipConcentration {
  top_n: number;
  /** Share of person-owned files the top_n people own; null when nobody owns one. */
  share: number | null;
  owners: ConcentrationOwner[];
}

export interface SiloModule {
  module: string;
  owner: OwnerRef;
  owned_files: number;
  file_count: number;
  /** owned_files / file_count, above 0.8. */
  share: number;
}

export interface QuietOwner extends OwnerRef {
  last_commit_at: string | null;
  files_owned: number;
  /** Owned files with a commit in the last 90 days. */
  changing_files_owned: number;
}

/** Automation (bots, coding agents) owns nothing in any of these figures. */
export interface OwnersSummary {
  truck_factor: number | null;
  people: number;
  automation_identities: number;
  owners: number;
  tracked_files: number;
  owned_files: number;
  /** Files whose bus factor is exactly 1. */
  single_owner_files: number;
  single_owner_hotspots: number;
  concentration: OwnershipConcentration;
  silo_modules: SiloModule[];
  activity_window_days: number;
  /** People with a commit in the window; a floor when activity_complete is false. */
  active_contributors: number;
  activity_complete: boolean;
  /** People with no commit in the window who still own files that changed in it. */
  quiet_owners: QuietOwner[];
  quiet_owners_total: number;
}

// ---------------------------------------------------------------------------
// /owners/knowledge-matrix: every code file, compact, for off-boarding
// ---------------------------------------------------------------------------

export interface KnowledgeMatrixAuthor {
  /** The owner key used by /owners/{key}. */
  key: string;
  name: string;
  email: string | null;
  last_commit_at: string | null;
  /** Committed in the 90 days before HEAD. */
  active: boolean;
}

/** [index into `authors`, share of the file's human commits (0-1)]. */
export type KnowledgeMatrixShare = [author: number, share: number];

export type KnowledgeMatrixFile = [
  dir: number,
  name: string,
  nloc: number,
  healthScore: number | null,
  isHotspot: boolean,
  busFactor: number,
  primaryOwner: number | null,
  authors: KnowledgeMatrixShare[],
  /** A commit in the 90 days before HEAD. */
  changed90d: boolean,
];

/**
 * Bots and coding agents are left out of every share. Each file lists its top
 * 5 people; anyone outside them holds less than the fifth share.
 */
export interface KnowledgeMatrix {
  authors: KnowledgeMatrixAuthor[];
  /** Directory per index; "" is the repository root. */
  dirs: string[];
  files: KnowledgeMatrixFile[];
}

/** A matrix row's repository-relative path. */
export function knowledgeMatrixPath(dirs: readonly string[], file: KnowledgeMatrixFile): string {
  const dir = dirs[file[0]];
  return dir ? `${dir}/${file[1]}` : file[1];
}

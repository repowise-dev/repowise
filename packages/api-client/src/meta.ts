/**
 * Meta endpoints: repowise version freshness + release changelog.
 * Powers the upgrade banner, what's-new view, and version footer.
 */

import { apiGet } from "./client";

export interface MetaVersion {
  server_version: string;
  latest_version: string | null;
  /** null when PyPI could not be reached (distinct from "up to date"). */
  update_available: boolean | null;
  upgrade_command: string;
  store_format_version: number | null;
  store_compatible: boolean | null;
  reindex_recommended: boolean;
  reindex_command: string | null;
}

export interface ChangelogSection {
  name: string;
  items: string[];
}

export interface ChangelogEntry {
  version: string;
  label: string | null;
  sections: ChangelogSection[];
}

export interface ChangelogData {
  entries: ChangelogEntry[];
}

export async function getMetaVersion(repoId?: string): Promise<MetaVersion> {
  return apiGet<MetaVersion>(
    "/api/meta/version",
    repoId ? { repo_id: repoId } : undefined,
  );
}

export async function getChangelog(limit = 20): Promise<ChangelogData> {
  return apiGet<ChangelogData>("/api/meta/changelog", { limit });
}

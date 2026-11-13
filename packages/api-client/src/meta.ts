/**
 * Meta endpoints: repowise version freshness + release changelog.
 * Powers the upgrade banner, what's-new view, and version footer.
 */

import type {
  ChangelogEntryModel,
  ChangelogHighlightModel,
  ChangelogResponse,
  ChangelogSectionModel,
  ReleaseStatsModel,
  VersionResponse,
} from "@repowise-dev/types/generated/http";
import { apiGet } from "./client";

export type MetaVersion = VersionResponse;
export type ChangelogSection = ChangelogSectionModel;
export type ChangelogHighlight = ChangelogHighlightModel;
export type ReleaseStats = ReleaseStatsModel;
/** `version` is `"unreleased"` for the next release's notes in a dev build. */
export type ChangelogEntry = ChangelogEntryModel;
export type ChangelogData = ChangelogResponse;

export async function getMetaVersion(repoId?: string): Promise<MetaVersion> {
  return apiGet<MetaVersion>(
    "/api/meta/version",
    repoId ? { repo_id: repoId } : undefined,
  );
}

/** `includeNewer` adds the releases between the running and the latest version
 *  (read from the latest release's changelog), for the upgrade banner. */
export async function getChangelog(limit = 20, includeNewer = false): Promise<ChangelogData> {
  return apiGet<ChangelogData>(
    "/api/meta/changelog",
    includeNewer ? { limit, include_newer: true } : { limit },
  );
}

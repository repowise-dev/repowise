/**
 * Installed plugins for one repository and "Sync now" for their sources. The
 * listing is `repowise plugins --format json`; a sync runs
 * `repowise sources sync` on the server and answers before it finishes.
 */

import type {
  PluginsResponse,
  SourcesSyncAccepted,
} from "@repowise-dev/types/generated/http";
import { apiGet, apiPost } from "./client";

export type { PluginsResponse, SourcesSyncAccepted };

/** Every installed plugin, every fetching source and its last sync, and the syncs running now. */
export async function getPlugins(repoId: string): Promise<PluginsResponse> {
  return apiGet<PluginsResponse>(`/api/repos/${encodeURIComponent(repoId)}/plugins`);
}

/** Start a sync of one source, or of every configured source when `source` is omitted. */
export async function syncSources(repoId: string, source?: string): Promise<SourcesSyncAccepted> {
  return apiPost<SourcesSyncAccepted>(
    `/api/repos/${encodeURIComponent(repoId)}/plugins/sync`,
    source ? { source } : {},
  );
}

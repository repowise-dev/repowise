/**
 * Event endpoint: typed events from consecutive index runs, pulled by time.
 *
 * Types come from `@repowise-dev/types/events`, re-exported here so a
 * consumer needs one import.
 */
import { apiGet } from "./client";
import type { CloudEvent, EventListResponse } from "@repowise-dev/types/events";

export type { CloudEvent, EventListResponse };

/**
 * Stored events, oldest first, from `since` (ISO time) on. `commit` (a full
 * sha) keeps those detected at that commit; `important` keeps broken
 * contracts, bus factor and test reach drops, and anything above info.
 */
export async function listEvents(
  repoId: string,
  opts?: { since?: string; kind?: string; limit?: number; commit?: string; important?: boolean },
): Promise<EventListResponse> {
  return apiGet<EventListResponse>(`/api/repos/${repoId}/events`, opts);
}

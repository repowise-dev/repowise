/**
 * What got better in a window: findings resolved, files whose score rose and
 * refactorings that landed. Positive only; with `author`, that person's wins.
 */
import { apiGet } from "./client";
import type { WinsResponse } from "@repowise-dev/types/history";

export type { WinsResponse };

export async function getWins(
  repoId: string,
  opts: {
    /** ISO 8601; the server defaults to 30 days ago. */
    since?: string;
    until?: string;
    /** Author email or exact name. */
    author?: string;
    /** Items per section, 1-50. */
    top?: number;
  } = {},
): Promise<WinsResponse> {
  return apiGet<WinsResponse>(`/api/repos/${repoId}/wins`, opts);
}

/**
 * Episode endpoints — the dated things that happened to a repository.
 *
 * Types come from `@repowise-dev/types/episodes` rather than this package's
 * local `./types`, following the direction `coupling`, `files` and
 * `blast-radius` already took; this module re-exports them so a consumer
 * needs one import.
 */
import { apiGet } from "./client";
import type {
  EpisodeCountsResponse,
  EpisodeDetail,
  EpisodeListResponse,
  EpisodeSummary,
  EpisodeTier,
} from "@repowise-dev/types/episodes";

export type {
  EpisodeCountsResponse,
  EpisodeDetail,
  EpisodeListResponse,
  EpisodeSummary,
  EpisodeTier,
};

/**
 * A page of episodes, newest first and without their bodies.
 *
 * Costs no git call, so it is safe to render a long list from. Open one
 * through `getEpisode` for the body and the checked currency verdict.
 *
 * A repository that has never derived episodes answers 200 with
 * `available: false` — check that before treating an empty `episodes` as
 * "everything was pruned".
 */
export async function listEpisodes(
  repoId: string,
  opts?: {
    /** Narrows to one tier. An unknown tier selects nothing, never all. */
    tier?: EpisodeTier;
    /** e.g. `code_fix`, `nested_repos`. */
    kind?: string;
    limit?: number;
    offset?: number;
  },
): Promise<EpisodeListResponse> {
  return apiGet<EpisodeListResponse>(`/api/repos/${repoId}/episodes`, opts);
}

/** Totals by tier and by kind, grouped server-side so the number is measured. */
export async function getEpisodeCounts(
  repoId: string,
): Promise<EpisodeCountsResponse> {
  return apiGet<EpisodeCountsResponse>(`/api/repos/${repoId}/episodes/counts`);
}

/**
 * Episodes bound at, above or below `path` — what happened to this file.
 *
 * `total` is the count for that path, so a file page can state a measured
 * number rather than the size of the window it fetched.
 */
export async function listEpisodesByFile(
  repoId: string,
  path: string,
  opts?: { limit?: number },
): Promise<EpisodeListResponse> {
  // `path` last: excess-property checking only fires on object literals, so a
  // caller passing a wider state object that happens to carry its own `path`
  // would otherwise clobber the argument and this page would confidently show
  // another file's history under a measured-looking total.
  return apiGet<EpisodeListResponse>(`/api/repos/${repoId}/episodes/by-file`, {
    ...opts,
    path,
  });
}

/**
 * One episode, whole, with `still_true` answered against the live checkout.
 *
 * The only episode call that shells out to git (~60 ms). Never call it per
 * row to decorate a list — `listEpisodes` already carries the free signal.
 */
export async function getEpisode(
  repoId: string,
  episodeId: string,
): Promise<EpisodeDetail> {
  return apiGet<EpisodeDetail>(`/api/repos/${repoId}/episodes/${episodeId}`);
}

"use client";

import useSWR from "swr";
import { getRepo, getRepoStats } from "@/lib/api/repos";
import { ApiClientError } from "@/lib/api/client";
import type { RepoResponse } from "@/lib/api/types";

/** Stop retrying when the resource simply doesn't exist. */
function skipRetryOn404(
  err: unknown,
  _key: string,
  _config: unknown,
  revalidate: (opts: { retryCount: number }) => void,
  { retryCount }: { retryCount: number },
) {
  if (err instanceof ApiClientError && err.status === 404) return;
  if (retryCount >= 3) return;
  setTimeout(
    () => revalidate({ retryCount: retryCount + 1 }),
    5_000 * Math.min(2 ** retryCount, 8),
  );
}

export function useRepo(repoId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<RepoResponse>(
    repoId ? `repo:${repoId}` : null,
    () => getRepo(repoId!),
    { refreshInterval: 30_000, onErrorRetry: skipRetryOn404 },
  );
  return { repo: data, error, isLoading, mutate };
}

/**
 * Files in the index, for naming the scope of a clean result ("across 5,612
 * files"). Null until it lands or when it cannot load, so a caller falls back
 * to copy without a count rather than claiming a number it never measured.
 */
export function useRepoFileCount(repoId: string): number | null {
  const { data } = useSWR(`repo-stats:${repoId}`, () => getRepoStats(repoId), {
    revalidateOnFocus: false,
  });
  return data?.file_count ?? null;
}

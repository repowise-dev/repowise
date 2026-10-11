"use client";

import useSWR from "swr";
import { getChangelog, getMetaVersion } from "@/lib/api/meta";

const SWR_OPTS = { revalidateOnFocus: false, revalidateOnReconnect: false };

/**
 * Server version + PyPI freshness. Shared key so the banner and the version
 * footer dedupe to a single request.
 */
export function useMetaVersion() {
  const { data, error, isLoading } = useSWR(
    "meta-version",
    () => getMetaVersion(),
    // Re-check hourly; the server caches the PyPI lookup so this is cheap.
    { ...SWR_OPTS, refreshInterval: 60 * 60 * 1000 },
  );
  return { meta: data ?? null, error: (error as Error | undefined) ?? null, isLoading };
}

/** Changelog entries. Fetched lazily (pass enabled=false until the view opens). */
export function useChangelog(enabled: boolean, limit = 20) {
  const { data, error, isLoading } = useSWR(
    enabled ? ["changelog", limit] : null,
    () => getChangelog(limit),
    SWR_OPTS,
  );
  return { entries: data?.entries ?? [], error: (error as Error | undefined) ?? null, isLoading };
}

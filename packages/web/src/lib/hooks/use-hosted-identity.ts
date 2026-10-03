"use client";

import useSWR from "swr";
import { getHostedIdentity } from "@/lib/api/platform";

/**
 * Sign-in state and tips switch, fetched once per session and
 * shared by every tip and publish button. `null` while loading or when the
 * server predates the endpoint, which callers treat as "show nothing".
 */
export function useHostedIdentity() {
  const { data } = useSWR("platform-identity", getHostedIdentity, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    shouldRetryOnError: false,
  });
  return { identity: data ?? null };
}

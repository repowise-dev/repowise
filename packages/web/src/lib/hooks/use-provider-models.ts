"use client";

import useSWR from "swr";
import { getProviderModels } from "@/lib/api/providers";
import type { ProviderModels } from "@/lib/api/types";

/** SWR key prefix; `useProviders` revalidates these after a key or URL change. */
export const PROVIDER_MODELS_KEY = "/api/providers/models";

/** A provider's models for the chat menu. Pass `null` while the menu is
 *  closed so nothing is fetched until it opens; the server caches the live
 *  list for a couple of minutes, so a long dedupe here costs nothing. */
export function useProviderModels(providerId: string | null, repoId?: string) {
  const { data, isLoading } = useSWR<ProviderModels>(
    providerId ? [PROVIDER_MODELS_KEY, providerId, repoId ?? ""] : null,
    () => getProviderModels(providerId!, repoId ? { repoId } : {}),
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  return { models: data, isLoading };
}

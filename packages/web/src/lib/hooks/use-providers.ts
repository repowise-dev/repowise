"use client";

import useSWR from "swr";
import {
  getProviders,
  setActiveProvider,
  addProviderKey,
  removeProviderKey,
} from "@/lib/api/providers";
import type { ProvidersResponse } from "@/lib/api/types";

export function useProviders() {
  const { data, mutate, isLoading } = useSWR<ProvidersResponse>(
    "/api/providers",
    getProviders,
    { revalidateOnFocus: false },
  );

  const activeProvider = data?.active.provider ?? null;
  const activeModel = data?.active.model ?? null;

  async function activate(providerId: string, model?: string) {
    await setActiveProvider(providerId, model);
    await mutate();
  }

  async function saveKey(providerId: string, key: string) {
    await addProviderKey(providerId, key);
    await mutate();
  }

  async function removeKey(providerId: string) {
    await removeProviderKey(providerId);
    await mutate();
  }

  return {
    providers: data?.providers ?? [],
    activeProvider,
    activeModel,
    isLoading,
    activate,
    saveKey,
    removeKey,
  };
}

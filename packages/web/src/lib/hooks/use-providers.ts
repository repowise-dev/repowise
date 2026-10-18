"use client";

import useSWR, { useSWRConfig } from "swr";
import {
  getProviders,
  setActiveProvider,
  addProviderKey,
  removeProviderKey,
  getProviderModels,
} from "@/lib/api/providers";
import type { ProvidersResponse } from "@/lib/api/types";
import { PROVIDER_MODELS_KEY } from "./use-provider-models";
import { useProviderValidation } from "./use-provider-validation";

export function useProviders(repoId?: string) {
  const { data, mutate, isLoading } = useSWR<ProvidersResponse>(
    repoId ? ["/api/providers", repoId] : "/api/providers",
    () => getProviders(repoId),
    { revalidateOnFocus: false },
  );

  // A key or endpoint change makes the chat menu's cached model lists stale.
  const { mutate: mutateAll } = useSWRConfig();
  const refreshModelLists = () =>
    mutateAll((key) => Array.isArray(key) && key[0] === PROVIDER_MODELS_KEY);

  const { validation, validate, forget } = useProviderValidation(repoId);

  const activeProvider = data?.active.provider ?? null;
  const activeModel = data?.active.model ?? null;

  // With repoId this writes the repo's .repowise/config.yaml (the CLI reads
  // it too); without it, the server-wide default.
  async function activate(
    providerId: string,
    model?: string,
    options: { baseUrl?: string } = {},
  ) {
    await mutate(await setActiveProvider(providerId, model, repoId, options), {
      revalidate: false,
    });
    void refreshModelLists();
  }

  function loadModels(
    providerId: string,
    options: { baseUrl?: string; refresh?: boolean } = {},
  ) {
    return getProviderModels(providerId, {
      ...options,
      ...(repoId ? { repoId } : {}),
    });
  }

  // Repo-scoped so D6 mirrors the key into the repo's .repowise/.env, making a
  // later CLI run in that repo see it. Without repoId the key stays
  // server-global and the CLI never picks it up.
  async function saveKey(providerId: string, key: string) {
    await addProviderKey(providerId, key, repoId);
    await mutate();
    void refreshModelLists();
  }

  async function removeKey(providerId: string) {
    await removeProviderKey(providerId, repoId);
    forget(providerId);
    await mutate();
    void refreshModelLists();
  }

  return {
    providers: data?.providers ?? [],
    embedders: data?.embedders ?? [],
    embedder: data?.active.embedder ?? null,
    activeProvider,
    activeModel,
    active: data?.active ?? { provider: null, model: null },
    isLoading,
    validation,
    activate,
    loadModels,
    saveKey,
    removeKey,
    validate,
  };
}

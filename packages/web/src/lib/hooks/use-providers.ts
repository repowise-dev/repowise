"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  getProviders,
  setActiveProvider,
  addProviderKey,
  removeProviderKey,
  validateProviderKey,
} from "@/lib/api/providers";
import type { ProvidersResponse } from "@/lib/api/types";
import type { ProviderValidationState } from "@repowise-dev/ui/settings/provider-settings";

export function useProviders(repoId?: string) {
  const { data, mutate, isLoading } = useSWR<ProvidersResponse>(
    repoId ? ["/api/providers", repoId] : "/api/providers",
    () => getProviders(repoId),
    { revalidateOnFocus: false },
  );

  const [validation, setValidation] = useState<
    Record<string, ProviderValidationState>
  >({});

  const activeProvider = data?.active.provider ?? null;
  const activeModel = data?.active.model ?? null;

  async function activate(providerId: string, model?: string) {
    await setActiveProvider(providerId, model, repoId);
    await mutate();
  }

  // Repo-scoped so D6 mirrors the key into the repo's .repowise/.env, making a
  // later CLI run in that repo see it. Without repoId the key stays
  // server-global and the CLI never picks it up.
  async function saveKey(providerId: string, key: string) {
    await addProviderKey(providerId, key, repoId);
    await mutate();
  }

  async function removeKey(providerId: string) {
    await removeProviderKey(providerId, repoId);
    setValidation((v) => {
      const next = { ...v };
      delete next[providerId];
      return next;
    });
    await mutate();
  }

  async function validate(providerId: string) {
    setValidation((v) => ({ ...v, [providerId]: { status: "testing" } }));
    try {
      const res = await validateProviderKey(providerId, repoId);
      setValidation((v) => ({
        ...v,
        [providerId]: res.ok
          ? { status: "ok" }
          : { status: "error", error: res.error },
      }));
    } catch (e) {
      setValidation((v) => ({
        ...v,
        [providerId]: { status: "error", error: (e as Error).message },
      }));
    }
  }

  return {
    providers: data?.providers ?? [],
    activeProvider,
    activeModel,
    active: data?.active ?? { provider: null, model: null },
    isLoading,
    validation,
    activate,
    saveKey,
    removeKey,
    validate,
  };
}

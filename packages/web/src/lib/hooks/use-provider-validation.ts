"use client";

import { useState } from "react";
import { validateProviderKey } from "@/lib/api/providers";
import type { ProviderValidationState } from "@repowise-dev/ui/settings/provider-settings";

/** Per-provider state of the live key test ("Test" on a provider row). */
export function useProviderValidation(repoId?: string) {
  const [validation, setValidation] = useState<Record<string, ProviderValidationState>>({});

  const put = (providerId: string, state: ProviderValidationState) =>
    setValidation((v) => ({ ...v, [providerId]: state }));

  function forget(providerId: string) {
    setValidation((v) => {
      const next = { ...v };
      delete next[providerId];
      return next;
    });
  }

  async function validate(providerId: string) {
    put(providerId, { status: "testing" });
    try {
      const res = await validateProviderKey(providerId, repoId);
      put(providerId, res.ok ? { status: "ok" } : { status: "error", error: res.error });
    } catch (e) {
      put(providerId, { status: "error", error: (e as Error).message });
    }
  }

  return { validation, validate, forget };
}

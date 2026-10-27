"use client";

import { toast } from "sonner";
import { ProviderSettings } from "@repowise-dev/ui/settings/provider-settings";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useProviders } from "@/lib/hooks/use-providers";
import { useTranslations } from "next-intl";
import { useProviderFormLabels } from "./use-provider-form-labels";

/**
 * Web data wrapper around the shared `ProviderSettings` shell. Owns the fetch,
 * the key mutations, validation, and the toasts. With `repoId` the choice is
 * that repo's (its `.repowise/config.yaml`, and keys mirror into its
 * `.repowise/.env`); without it, the server-wide default. The shell stays
 * presentational and hosted-portable.
 */
export function ProviderSettingsPanel({ repoId }: { repoId?: string }) {
  const {
    providers,
    active,
    validation,
    isLoading,
    saveKey,
    removeKey,
    activate,
    loadModels,
    validate,
  } = useProviders(repoId);

  const t = useTranslations("settings");
  const f = useTranslations("settings.providerForm");
  const labels = useProviderFormLabels(Boolean(repoId));

  if (isLoading) {
    return (
      <div className="space-y-2.5">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }


  async function handleAddKey(providerId: string, key: string) {
    try {
      await saveKey(providerId, key);
      toast.success(t("providerPanel.keySaved"), {
        description: t("providerPanel.keySavedDescription"),
      });
    } catch (e) {
      toast.error(t("providerPanel.keySaveFailed"), {
        description: toFriendlyMessage(e),
      });
    }
  }

  async function handleRemoveKey(providerId: string) {
    try {
      await removeKey(providerId);
      toast.info(t("providerPanel.keyRemoved"));
    } catch (e) {
      toast.error(t("providerPanel.keyRemoveFailed"), {
        description: toFriendlyMessage(e),
      });
    }
  }

  async function handleSetActive(
    providerId: string,
    model?: string,
    options?: { baseUrl?: string },
  ) {
    try {
      await activate(providerId, model, options);
      if (repoId) toast.success(f("saved"), { description: f("savedDescription") });
      else toast.success(f("savedGlobal"));
    } catch (e) {
      toast.error(t("providerPanel.activateFailed"), {
        description: toFriendlyMessage(e),
      });
      throw e;
    }
  }

  return (
    <ProviderSettings
      providers={providers}
      active={active}
      onAddKey={handleAddKey}
      onRemoveKey={handleRemoveKey}
      onSetActive={handleSetActive}
      onLoadModels={loadModels}
      onValidate={validate}
      validation={validation}
      labels={labels}
    />
  );
}

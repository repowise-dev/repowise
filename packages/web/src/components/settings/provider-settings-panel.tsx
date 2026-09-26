"use client";

import { toast } from "sonner";
import { ProviderSettings } from "@repowise-dev/ui/settings/provider-settings";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useProviders } from "@/lib/hooks/use-providers";
import { useTranslations } from "next-intl";

/**
 * Web data wrapper around the shared `ProviderSettings` shell. Owns the fetch,
 * the repo-scoped key mutations (so D6 mirrors the key into `.repowise/.env`),
 * validation, and the toasts. The shell stays presentational and hosted-portable.
 */
export function ProviderSettingsPanel({ repoId }: { repoId: string }) {
  const {
    providers,
    active,
    validation,
    isLoading,
    saveKey,
    removeKey,
    activate,
    validate,
  } = useProviders(repoId);

  const t = useTranslations("settings");

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

  async function handleSetActive(providerId: string, model?: string) {
    try {
      await activate(providerId, model);
    } catch (e) {
      toast.error(t("providerPanel.activateFailed"), {
        description: toFriendlyMessage(e),
      });
    }
  }

  return (
    <ProviderSettings
      providers={providers}
      active={active}
      onAddKey={handleAddKey}
      onRemoveKey={handleRemoveKey}
      onSetActive={handleSetActive}
      onValidate={validate}
      validation={validation}
    />
  );
}

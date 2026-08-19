"use client";

import { toast } from "sonner";
import { ProviderSettings } from "@repowise-dev/ui/settings/provider-settings";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useProviders } from "@/lib/hooks/use-providers";

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
      toast.success("API key saved", {
        description: "Written to this repo. Test it to confirm it works.",
      });
    } catch (e) {
      toast.error("Couldn't save the key", { description: toFriendlyMessage(e) });
    }
  }

  async function handleRemoveKey(providerId: string) {
    try {
      await removeKey(providerId);
      toast.info("API key removed");
    } catch (e) {
      toast.error("Couldn't remove the key", { description: toFriendlyMessage(e) });
    }
  }

  async function handleSetActive(providerId: string, model?: string) {
    try {
      await activate(providerId, model);
    } catch (e) {
      toast.error("Couldn't set the active provider", {
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

"use client";

import { toast } from "sonner";
import {
  ProviderSettings,
  type ProviderSettingsLabels,
} from "@repowise-dev/ui/settings/provider-settings";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useProviders } from "@/lib/hooks/use-providers";
import { useTranslations } from "next-intl";

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

  if (isLoading) {
    return (
      <div className="space-y-2.5">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  const labels: ProviderSettingsLabels = {
    model: f("model"),
    baseUrl: f("baseUrl"),
    baseUrlHint: f("baseUrlHint"),
    baseUrlInvalid: f("baseUrlInvalid"),
    localServer: f("localServer"),
    testConnection: f("testConnection"),
    connected: (count) => f("connected", { count }),
    save: f("save"),
    useProvider: (name) => f("useProvider", { name }),
    cancel: f("cancel"),
    regenerateNotice: repoId ? f("regenerateNotice") : f("regenerateNoticeGlobal"),
    unconfirmedUrl: (url) => f("unconfirmedUrl", { url }),
    trustUrl: f("trustUrl"),
    active: f("active"),
    noKeyRequired: f("noKeyRequired"),
    keySet: f("keySet"),
    noKey: f("noKey"),
    use: f("use"),
    useAria: (name) => f("useAria", { name }),
    test: f("test"),
    testAria: (name) => f("testAria", { name }),
    addKey: f("addKey"),
    updateKey: f("updateKey"),
    removeKeyAria: (name) => f("removeKeyAria", { name }),
    keyAria: (name) => f("keyAria", { name }),
    keyPlaceholder: (name) => f("keyPlaceholder", { name }),
    saveKeyAria: (name) => f("saveKeyAria", { name }),
    connectionVerified: f("connectionVerified"),
    connectionFailed: f("connectionFailed"),
    provenance: (source) => {
      if (source === "config.yaml") return f("sourceConfig");
      if (source === "global") return f("sourceGlobal");
      if (source === "default") return f("sourceDefault");
      if (source === "autodetect") return f("sourceAutodetect");
      if (source.startsWith("env:")) return f("sourceEnv", { name: source.slice(4) });
      return null;
    },
  };

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

"use client";

import { useState } from "react";
import { providerSetupHref } from "@/lib/utils/page-href";
import { useTranslations } from "next-intl";
import { ModelSelector as ModelSelectorShell } from "@repowise-dev/ui/chat/model-selector";
import { useProviders } from "@/lib/hooks/use-providers";
import { useProviderModels } from "@/lib/hooks/use-provider-models";

/** The per-conversation model menu. A pick is sent with each request and
 *  never written to the repo's config; that is the Settings page's job. */
export function ModelSelector({
  repoId,
  activeProvider: controlledProvider,
  activeModel: controlledModel,
  onSelect,
}: {
  repoId?: string;
  activeProvider?: string | null;
  activeModel?: string | null;
  onSelect?: (provider: string, model: string) => void;
}) {
  const t = useTranslations("chat.modelMenu");
  const { providers, activeProvider, activeModel, isLoading } = useProviders(repoId);
  // Only the provider the open menu shows is fetched.
  const [browsing, setBrowsing] = useState<string | null>(null);
  const { models, isLoading: modelsLoading } = useProviderModels(browsing, repoId);

  return (
    <ModelSelectorShell
      providers={providers}
      activeProvider={controlledProvider ?? activeProvider}
      activeModel={controlledModel ?? activeModel}
      defaultProvider={activeProvider}
      defaultModel={activeModel}
      isLoading={isLoading}
      onActivate={(id, model) => onSelect?.(id, model)}
      onBrowse={setBrowsing}
      {...(models ? { models: models.models } : {})}
      modelsLoading={modelsLoading}
      settingsHref={repoId ? providerSetupHref(repoId) : "/settings/models"}
      labels={{
        dialog: t("dialog"),
        provider: t("provider"),
        search: t("search"),
        useTyped: (id) => t("useTyped", { id }),
        repoDefault: t("repoDefault"),
        providerDefault: t("providerDefault"),
        loading: t("loading"),
        noMatch: t("noMatch"),
        configure: t("configure"),
        needsSetup: t("needsSetup"),
      }}
    />
  );
}

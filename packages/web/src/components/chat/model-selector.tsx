"use client";

import { ModelSelector as ModelSelectorShell } from "@repowise-dev/ui/chat/model-selector";
import { useProviders } from "@/lib/hooks/use-providers";

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
  const {
    providers,
    activeProvider,
    activeModel,
    isLoading,
  } = useProviders(repoId);

  return (
    <ModelSelectorShell
      providers={providers}
      activeProvider={controlledProvider ?? activeProvider}
      activeModel={controlledModel ?? activeModel}
      isLoading={isLoading}
      onActivate={(id, model) => onSelect?.(id, model)}
      settingsHref="/settings"
    />
  );
}

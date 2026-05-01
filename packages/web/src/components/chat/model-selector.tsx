"use client";

import { ModelSelector as ModelSelectorShell } from "@repowise-dev/ui/chat/model-selector";
import { useProviders } from "@/lib/hooks/use-providers";

export function ModelSelector() {
  const {
    providers,
    activeProvider,
    activeModel,
    isLoading,
    activate,
    saveKey,
  } = useProviders();

  return (
    <ModelSelectorShell
      providers={providers}
      activeProvider={activeProvider}
      activeModel={activeModel}
      isLoading={isLoading}
      onActivate={(id, model) => activate(id, model)}
      onSaveKey={saveKey}
    />
  );
}

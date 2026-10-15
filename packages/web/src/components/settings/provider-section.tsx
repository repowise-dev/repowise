"use client";

import React from "react";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { SettingsRow, SettingsRows, EnvVarLine } from "@repowise-dev/ui/settings";
import { useTranslations } from "next-intl";
import { useProviders } from "@/lib/hooks/use-providers";
import { ProviderSettingsPanel } from "./provider-settings-panel";

/**
 * The server-wide default model: the same form as a repository's settings,
 * without a repository, so it writes the server default that every repo
 * without its own `.repowise/config.yaml` model uses. The embedder is read
 * from the server's environment, so it is shown, not edited.
 */
export function ProviderSection() {
  const t = useTranslations("settings");
  const { embedder, embedders } = useProviders();
  const embedderInfo = embedders.find((entry) => entry.id === embedder);
  const code = (chunks: React.ReactNode) => (
    <code className="font-mono text-[var(--color-text-secondary)]">{chunks}</code>
  );

  return (
    <OverviewSection title={t("provider.title")} description={t("provider.description")}>
      <ProviderSettingsPanel />
      {embedder && (
        <SettingsRows className="mt-4">
          <SettingsRow label={t("provider.embedderLabel")} hint={t("provider.embedderHint")}>
            <div className="space-y-2">
              <p className="font-mono text-sm text-[var(--color-text-primary)]">{embedder}</p>
              {embedderInfo?.semantic === false ? (
                <EnvVarLine vars={[]} note={t.rich("provider.embedderOffNote", { code })} />
              ) : (
                <EnvVarLine
                  vars={embedderInfo?.env_vars ?? []}
                  note={t.rich("provider.embedderOnNote", {
                    code,
                    env: `REPOWISE_EMBEDDER=${embedder}`,
                  })}
                />
              )}
            </div>
          </SettingsRow>
        </SettingsRows>
      )}
    </OverviewSection>
  );
}

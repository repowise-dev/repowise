"use client";

import { useEffect, useState } from "react";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { CopyLine, SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
import { useTranslations } from "next-intl";

/**
 * Best-effort server URL for webhook registration: the configured API URL
 * when set, else the dashboard origin (API requests are proxied through it
 * via Next rewrites, so webhooks reach the backend the same way).
 */
export function resolveWebhookBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_REPOWISE_API_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window !== "undefined") return window.location.origin;
  return "http://localhost:7337";
}

export function WebhookSection() {
  const t = useTranslations("settings");
  const [serverUrl, setServerUrl] = useState("http://localhost:7337");
  // Resolved in an effect so SSR and the first client render agree.
  useEffect(() => {
    setServerUrl(resolveWebhookBaseUrl());
  }, []);

  return (
    <OverviewSection
      title={t("webhook.title")}
      description={t("webhook.description")}
    >
      <SettingsRows>
        <SettingsRow label="GitHub" hint={t("webhook.githubHint")}>
          <CopyLine value={`${serverUrl}/api/webhooks/github`} />
        </SettingsRow>

        <SettingsRow label="GitLab" hint={t("webhook.gitlabHint")}>
          <CopyLine value={`${serverUrl}/api/webhooks/gitlab`} />
        </SettingsRow>

        <SettingsRow
          label={t("webhook.signatureLabel")}
          hint={t("webhook.signatureHint")}
        >
          <div className="space-y-1.5">
            <p className="font-mono text-xs text-[var(--color-text-secondary)]">
              REPOWISE_GITHUB_WEBHOOK_SECRET=your-secret
            </p>
            <p className="font-mono text-xs text-[var(--color-text-secondary)]">
              REPOWISE_GITLAB_WEBHOOK_TOKEN=your-token
            </p>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              {t("webhook.serverOnly")}
            </p>
          </div>
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}

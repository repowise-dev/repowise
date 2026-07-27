"use client";

import { useEffect, useState } from "react";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { CopyLine, SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";

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
  const [serverUrl, setServerUrl] = useState("http://localhost:7337");
  // Resolved in an effect so SSR and the first client render agree.
  useEffect(() => {
    setServerUrl(resolveWebhookBaseUrl());
  }, []);

  return (
    <OverviewSection
      title="Webhooks"
      description="Register one of these in GitHub or GitLab and a push re-syncs the index. The URLs use this dashboard's address, so substitute a public hostname if the host cannot reach it."
    >
      <SettingsRows>
        <SettingsRow label="GitHub" hint="Content type: application/json.">
          <CopyLine value={`${serverUrl}/api/webhooks/github`} />
        </SettingsRow>

        <SettingsRow label="GitLab" hint="Push events.">
          <CopyLine value={`${serverUrl}/api/webhooks/gitlab`} />
        </SettingsRow>

        <SettingsRow
          label="Signature verification"
          hint="Omit both to skip verification while developing locally."
        >
          <div className="space-y-1.5">
            <p className="font-mono text-xs text-[var(--color-text-secondary)]">
              REPOWISE_GITHUB_WEBHOOK_SECRET=your-secret
            </p>
            <p className="font-mono text-xs text-[var(--color-text-secondary)]">
              REPOWISE_GITLAB_WEBHOOK_TOKEN=your-token
            </p>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              Set on the server, not here.
            </p>
          </div>
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}

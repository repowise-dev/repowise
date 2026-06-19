import type { Metadata } from "next";
import { ConnectionSection } from "@/components/settings/connection-section";
import { ProviderSection } from "@/components/settings/provider-section";
import { WebhookSection } from "@/components/settings/webhook-section";
import { McpSection } from "@/components/settings/mcp-section";
import { McpToolsSection } from "@/components/settings/mcp-tools-section";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <div className="p-4 sm:p-6 max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">Settings</h1>
        <p className="text-sm text-[var(--color-text-secondary)] mt-1">
          API connection, provider defaults, and integration config.
        </p>
      </div>

      <ConnectionSection />
      <ProviderSection />
      <WebhookSection />
      <McpSection />
      <McpToolsSection />

      <p className="text-xs text-[var(--color-text-tertiary)]">
        Per-repository options (sync, exclude patterns, deletion) live on each
        repo&apos;s own Settings page, reachable from its sidebar.
      </p>
    </div>
  );
}

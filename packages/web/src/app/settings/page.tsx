import type { Metadata } from "next";
import Link from "next/link";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ConnectionSection } from "@/components/settings/connection-section";
import { ProviderSection } from "@/components/settings/provider-section";
import { WebhookSection } from "@/components/settings/webhook-section";
import { McpSection } from "@/components/settings/mcp-section";
import { McpToolsSection } from "@/components/settings/mcp-tools-section";
import { DisplaySection } from "@/components/settings/display-section";
import {
  SettingsGroup,
  SettingsGroupNav,
  settingsGroupTitles,
  type SettingsGroupId,
} from "@/components/settings/settings-groups";
import { GlobalSetupLinks, HostedAgentsRow, TelemetryRow } from "@/components/setup/setup-settings";
import { listRepos } from "@/lib/api/repos";
import { getTranslations } from "next-intl/server";
import { HostedNudgeSlot } from "@/components/hosted/hosted-nudge-slot";

export const metadata: Metadata = { title: "Settings" };

/** Coverage is per repository, so this page has no Coverage group. */
const GROUPS: SettingsGroupId[] = ["setup", "agents", "models", "sync", "server", "privacy"];

/**
 * Global settings.
 *
 * Was seven `Card`s in a hand-rolled `p-4 sm:p-6 max-w-2xl` frame, while the
 * per-repo settings page used `PageShell` — two settings surfaces, two layout
 * systems, two heading scales for the same role. Both run `PageShell` and
 * `OverviewSection` now, and everything on the page autosaves.
 *
 * Sections are grouped by what people manage, under the same group names as
 * the repo settings page, with Privacy last.
 *
 * `narrow` rather than the default 1280: this is a form, and a control that
 * stretches to 1280 loses the relationship between its label and itself.
 */
export default async function SettingsPage() {
  const t = await getTranslations("views.globalSettings");
  const tg = await getTranslations("settings.groups");
  const titles = settingsGroupTitles(tg);
  const repos = await listRepos().catch(() => []);

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      maxWidth="narrow"
    >
      <SettingsGroupNav groups={GROUPS} label={tg("navLabel")} titles={titles} />

      <SettingsGroup id="setup" title={titles.setup}>
        <GlobalSetupLinks repos={repos} />
      </SettingsGroup>

      <SettingsGroup id="agents" title={titles.agents} description={tg("agents.description")}>
        <HostedNudgeSlot candidates={["mcp"]} />
        <McpSection />
        <McpToolsSection />
        <HostedAgentsRow />
      </SettingsGroup>

      <SettingsGroup id="models" title={titles.models}>
        <ProviderSection />
      </SettingsGroup>

      <SettingsGroup id="sync" title={titles.sync}>
        <WebhookSection />
      </SettingsGroup>

      <SettingsGroup id="server" title={titles.server}>
        <ConnectionSection />
        <DisplaySection />
      </SettingsGroup>

      <SettingsGroup id="privacy" title={titles.privacy}>
        <TelemetryRow />
      </SettingsGroup>

      <p className="border-t border-[var(--color-border-default)] pt-6 text-xs text-[var(--color-text-tertiary)]">
        {t.rich("footer", {
          link: (chunks) => (
            <Link
              href="/"
              className="text-[var(--color-accent-primary)] hover:underline"
            >
              {chunks}
            </Link>
          ),
        })}
      </p>
    </PageShell>
  );
}

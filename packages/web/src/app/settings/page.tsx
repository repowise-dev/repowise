import type { Metadata } from "next";
import Link from "next/link";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ConnectionSection } from "@/components/settings/connection-section";
import { ProviderSection } from "@/components/settings/provider-section";
import { WebhookSection } from "@/components/settings/webhook-section";
import { McpSection } from "@/components/settings/mcp-section";
import { McpToolsSection } from "@/components/settings/mcp-tools-section";
import { DisplaySection } from "@/components/settings/display-section";
import { getTranslations } from "next-intl/server";

export const metadata: Metadata = { title: "Settings" };

/**
 * Global settings.
 *
 * Was seven `Card`s in a hand-rolled `p-4 sm:p-6 max-w-2xl` frame, while the
 * per-repo settings page used `PageShell` — two settings surfaces, two layout
 * systems, two heading scales for the same role. Both run `PageShell` and
 * `OverviewSection` now, and everything on the page autosaves.
 *
 * `max-w-3xl` rather than the default 1280: this is a form, and a control that
 * stretches to 1280 loses the relationship between its label and itself.
 */
export default async function SettingsPage() {
  const t = await getTranslations("views.globalSettings");

  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      className="max-w-3xl"
    >
      <ConnectionSection />
      <ProviderSection />
      <DisplaySection />
      <McpSection />
      <McpToolsSection />
      <WebhookSection />

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

import { getTranslations } from "next-intl/server";
import { WebhookSection } from "@/components/settings/webhook-section";
import { SettingsGroup } from "@/components/settings/settings-groups";

/** Webhook addresses, shared by every repository on this server. */
export default async function SyncSettingsPage() {
  const t = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");
  return (
    <SettingsGroup title={tg("sync.title")} description={t("webhook.description")}>
      <WebhookSection />
    </SettingsGroup>
  );
}

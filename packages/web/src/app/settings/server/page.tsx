import { getTranslations } from "next-intl/server";
import { ConnectionSection } from "@/components/settings/connection-section";
import { SettingsGroup } from "@/components/settings/settings-groups";

/** How this dashboard reaches the repowise server. */
export default async function ServerSettingsPage() {
  const t = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");
  return (
    <SettingsGroup title={tg("server.title")} description={t("connection.description")}>
      <ConnectionSection />
    </SettingsGroup>
  );
}

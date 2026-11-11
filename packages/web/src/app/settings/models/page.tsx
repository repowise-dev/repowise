import { getTranslations } from "next-intl/server";
import { ProviderSection } from "@/components/settings/provider-section";
import { SettingsGroup } from "@/components/settings/settings-groups";

/** The server-wide default model, for every repository without its own. */
export default async function ModelsSettingsPage() {
  const t = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");
  return (
    <SettingsGroup title={tg("models.title")} description={t("provider.description")}>
      <ProviderSection />
    </SettingsGroup>
  );
}

import { getTranslations } from "next-intl/server";
import { DisplaySection } from "@/components/settings/display-section";
import { SettingsGroup } from "@/components/settings/settings-groups";

/** Preferences for this browser. */
export default async function DisplaySettingsPage() {
  const t = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");
  return (
    <SettingsGroup title={tg("display.title")} description={t("display.description")}>
      <DisplaySection />
    </SettingsGroup>
  );
}

import { getTranslations } from "next-intl/server";
import { SettingsGroup } from "@/components/settings/settings-groups";
import { TelemetryRows } from "@/components/setup/setup-settings";

/** Anonymous usage telemetry, checked and turned off from the terminal. Always last. */
export default async function PrivacySettingsPage() {
  const t = await getTranslations("setup.settings");
  const tg = await getTranslations("settings.groups");
  return (
    <SettingsGroup title={tg("privacy.title")} description={t("telemetryDescription")}>
      <TelemetryRows />
    </SettingsGroup>
  );
}

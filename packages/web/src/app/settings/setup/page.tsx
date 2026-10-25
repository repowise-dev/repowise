import { getTranslations } from "next-intl/server";
import { listRepos } from "@/lib/api/repos";
import { SettingsGroup } from "@/components/settings/settings-groups";
import { SetupRepoLinks } from "@/components/setup/setup-settings";

/** Setup is per repository, so global settings links to each one's. */
export default async function SetupSettingsPage() {
  const t = await getTranslations("setup.settings");
  const tg = await getTranslations("settings.groups");
  const repos = await listRepos().catch(() => []);
  return (
    <SettingsGroup title={tg("setup.title")} description={t("globalLead")}>
      <SetupRepoLinks repos={repos} />
    </SettingsGroup>
  );
}

import { getTranslations } from "next-intl/server";
import { SettingsGroup } from "@/components/settings/settings-groups";
import { PluginsSettingsPanel } from "@/components/settings/plugins-settings-panel";

interface Props {
  params: Promise<{ id: string }>;
}

/** Installed plugins, the sources they fetch from, and Sync now. */
export default async function RepoPluginsSettingsPage({ params }: Props) {
  const { id } = await params;
  const tg = await getTranslations("settings.groups");
  return (
    <SettingsGroup title={tg("plugins.title")} description={tg("plugins.description")}>
      <PluginsSettingsPanel repoId={id} />
    </SettingsGroup>
  );
}

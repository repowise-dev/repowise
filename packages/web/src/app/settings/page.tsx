import { SettingsRedirect } from "@/components/settings/settings-nav";
import { GLOBAL_SETTINGS_BASE, GLOBAL_SETTINGS_GROUPS } from "@/components/settings/settings-groups";

/** `/settings` opens the first group, or the one an old `#group-<id>` link named. */
export default function SettingsIndexPage() {
  return <SettingsRedirect base={GLOBAL_SETTINGS_BASE} groups={GLOBAL_SETTINGS_GROUPS} />;
}

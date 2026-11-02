import { SettingsRedirect } from "@/components/settings/settings-nav";
import { REPO_SETTINGS_GROUPS, repoSettingsBase } from "@/components/settings/settings-groups";

interface Props {
  params: Promise<{ id: string }>;
}

/**
 * `/repos/[id]/settings` opens Setup, or the group an old link named
 * (`#provider` opens Models).
 */
export default async function RepoSettingsIndexPage({ params }: Props) {
  const { id } = await params;
  return <SettingsRedirect base={repoSettingsBase(id)} groups={REPO_SETTINGS_GROUPS} />;
}

import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { getRepo } from "@/lib/api/repos";
import { SettingsGroup } from "@/components/settings/settings-groups";
import { SettingsRegistryPanel } from "@/components/settings/settings-registry-panel";

interface Props {
  params: Promise<{ id: string }>;
}

/** Every repowise setting for this repository, generated from the settings registry. */
export default async function RepoAllSettingsPage({ params }: Props) {
  const { id } = await params;
  const tg = await getTranslations("settings.groups");
  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }
  return (
    <SettingsGroup title={tg("all.title")} description={tg("all.description")}>
      <SettingsRegistryPanel repoId={id} repoName={repo.name} />
    </SettingsGroup>
  );
}

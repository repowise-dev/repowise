import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { SettingsRows } from "@repowise-dev/ui/settings";
import { getRepo } from "@/lib/api/repos";
import { RepoSetupRows } from "@/components/setup/setup-settings";
import { SettingsGroup } from "@/components/settings/settings-groups";

const COVERAGE_ROWS = ["coverage"];

interface Props {
  params: Promise<{ id: string }>;
}

/** The coverage report this repository reads. */
export default async function RepoCoverageSettingsPage({ params }: Props) {
  const { id } = await params;
  const tg = await getTranslations("settings.groups");
  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }
  return (
    <SettingsGroup title={tg("coverage.title")} description={tg("coverage.description")}>
      <SettingsRows>
        <RepoSetupRows repoId={id} repoName={repo.name} ids={COVERAGE_ROWS} />
      </SettingsRows>
    </SettingsGroup>
  );
}

import Link from "next/link";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { SettingsAdvanced, SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
import { getRepo } from "@/lib/api/repos";
import { getCoordinatorHealth } from "@/lib/api/health";
import { OperationsPanel } from "@/components/repos/operations-panel";
import { CoordinatorHealthPanel } from "@/components/repos/coordinator-health-panel";
import { RepoSetupRows } from "@/components/setup/setup-settings";
import {
  SETTINGS_LINK,
  SettingsBlock,
  SettingsGroup,
  settingsHref,
} from "@/components/settings/settings-groups";

const SYNC_ROWS = ["sync", "rewrite"];

interface Props {
  params: Promise<{ id: string }>;
}

/** Sync now, the hooks that keep the index current, and the shared webhooks. */
export default async function RepoSyncSettingsPage({ params }: Props) {
  const { id } = await params;
  const t = await getTranslations("views.repoSettings");
  const ts = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");
  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }
  const coordinatorHealth = await getCoordinatorHealth(id).catch(() => null);

  return (
    <SettingsGroup title={tg("sync.title")} description={t("syncDescription")}>
      <OperationsPanel repoId={id} repoName={repo.name} />
      <SettingsRows>
        <RepoSetupRows repoId={id} repoName={repo.name} ids={SYNC_ROWS} />
        <SettingsRow label={ts("scope.webhooksLabel")} hint={ts("scope.webhooksHint")}>
          <div className="flex sm:justify-end">
            <Link href={settingsHref("sync")} className={SETTINGS_LINK}>
              {ts("scope.allRepos")}
            </Link>
          </div>
        </SettingsRow>
      </SettingsRows>
      <SettingsAdvanced label={ts("advanced")} hint={ts("advancedHints.sync")}>
        <SettingsBlock title={t("healthTitle")} description={t("healthDescription")}>
          <CoordinatorHealthPanel repoId={id} initial={coordinatorHealth} />
        </SettingsBlock>
      </SettingsAdvanced>
    </SettingsGroup>
  );
}

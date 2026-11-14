import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { SettingsAdvanced, SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
import { DecisionCaptureSettingsWrapper } from "@/components/decisions/decision-capture-settings-wrapper";
import { RefactoringSettingsSection } from "@/components/repos/refactoring-settings-section";
import { ExportDocsRow } from "@/components/repos/operations-panel";
import { ProviderSettingsPanel } from "@/components/settings/provider-settings-panel";
import {
  SETTINGS_LINK,
  SettingsBlock,
  SettingsGroup,
  settingsHref,
} from "@/components/settings/settings-groups";

interface Props {
  params: Promise<{ id: string }>;
}

/**
 * This repository's model, with the server default one link away. Decision
 * capture, code generation and the docs export sit under Advanced.
 */
export default async function RepoModelsSettingsPage({ params }: Props) {
  const { id } = await params;
  const t = await getTranslations("views.repoSettings");
  const ts = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");

  return (
    <SettingsGroup title={tg("models.title")} description={t("providerDescription")}>
      <ProviderSettingsPanel repoId={id} />
      <SettingsRows>
        <SettingsRow label={ts("scope.defaultModelLabel")} hint={ts("scope.defaultModelHint")}>
          <div className="flex sm:justify-end">
            <Link href={settingsHref("models")} className={SETTINGS_LINK}>
              {ts("scope.allRepos")}
            </Link>
          </div>
        </SettingsRow>
      </SettingsRows>
      <SettingsAdvanced label={ts("advanced")} hint={ts("advancedHints.models")}>
        <SettingsBlock title={t("captureTitle")} description={t("captureDescription")}>
          <DecisionCaptureSettingsWrapper repoId={id} />
        </SettingsBlock>
        <SettingsBlock title={t("refactoringTitle")} description={t("refactoringDescription")}>
          <RefactoringSettingsSection repoId={id} />
        </SettingsBlock>
        <SettingsRows className="border-t border-[var(--color-border-default)]">
          <ExportDocsRow repoId={id} />
        </SettingsRows>
      </SettingsAdvanced>
    </SettingsGroup>
  );
}

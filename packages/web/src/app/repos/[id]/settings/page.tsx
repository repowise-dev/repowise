import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { DecisionCaptureSettingsWrapper } from "@/components/decisions/decision-capture-settings-wrapper";
import { RefactoringSettingsSection } from "@/components/repos/refactoring-settings-section";
import { ProviderSettingsPanel } from "@/components/settings/provider-settings-panel";
import { getRepo } from "@/lib/api/repos";
import { getCoordinatorHealth } from "@/lib/api/health";
import { RepoSettingsFormWrapper as RepoSettingsForm } from "@/components/repos/repo-settings-form-wrapper";
import { CoordinatorHealthPanel } from "@/components/repos/coordinator-health-panel";
import { DeleteRepoButton } from "@/components/repos/delete-repo-button";
import { OperationsPanel } from "@/components/repos/operations-panel";
import { getTranslations } from "next-intl/server";
import { PublishPanel } from "@/components/hosted/publish";
import {
  SettingsGroup,
  SettingsGroupNav,
  settingsGroupTitles,
  type SettingsGroupId,
} from "@/components/settings/settings-groups";
import { RepoAgentRows, RepoSetupRows, SetupLinkRow } from "@/components/setup/setup-settings";

/** Telemetry is per machine, so Privacy lives on global settings only. */
const GROUPS: SettingsGroupId[] = ["setup", "agents", "models", "sync", "coverage", "server"];
const SYNC_ROWS = ["sync", "rewrite"];
const COVERAGE_ROWS = ["coverage"];

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const repo = await getRepo(id);
    return { title: `${repo.name} — Settings` };
  } catch {
    return { title: "Settings" };
  }
}

/**
 * Per-repository settings.
 *
 * Grouped by what people manage, under the same group names as global
 * settings. The Setup page's own rows (agents, hooks, coverage) render here
 * too, with the same controls.
 *
 * Was seven `Card`s and two `Separator`s. Sections carry the grouping now, and
 * the page shares its frame and its vocabulary with global settings.
 *
 * `WebhookSection` used to render here in full *and* on the global page, while
 * this page separately linked out to global settings for connection and MCP —
 * pointing elsewhere for two things and copying a third. Webhooks are a server
 * address, not a repo preference, so they live in one place and this page links
 * to them with everything else.
 */
export default async function RepoSettingsPage({ params }: Props) {
  const { id } = await params;
  const t = await getTranslations("views.repoSettings");

  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }

  const coordinatorHealth = await getCoordinatorHealth(id).catch(() => null);
  const tg = await getTranslations("settings.groups");
  const titles = settingsGroupTitles(tg);

  return (
    <PageShell
      title={t("title")}
      description={t("description", { repo: repo.name })}
      maxWidth="narrow"
    >
      <SettingsGroupNav groups={GROUPS} label={tg("navLabel")} titles={titles} />

      <SettingsGroup id="setup" title={titles.setup}>
        <SetupLinkRow repoId={id} />
        <OverviewSection
          title={t("generalTitle")}
          description={t("generalDescription")}
        >
          <RepoSettingsForm repo={repo} />
        </OverviewSection>

        <OverviewSection
          title={t("healthTitle")}
          description={t("healthDescription")}
        >
          <CoordinatorHealthPanel repoId={id} initial={coordinatorHealth} />
        </OverviewSection>
      </SettingsGroup>

      <SettingsGroup id="agents" title={titles.agents} description={tg("agents.description")}>
        <RepoAgentRows repoId={id} repoName={repo.name} />
      </SettingsGroup>

      <SettingsGroup id="models" title={titles.models}>
        <OverviewSection
          id="provider"
          title={t("providerTitle")}
          description={t("providerDescription")}
        >
          <ProviderSettingsPanel repoId={id} />
        </OverviewSection>

        <OverviewSection
          title={t("captureTitle")}
          description={t("captureDescription")}
        >
          <DecisionCaptureSettingsWrapper repoId={id} />
        </OverviewSection>

        <OverviewSection
          title={t("refactoringTitle")}
          description={t("refactoringDescription")}
        >
          <RefactoringSettingsSection repoId={id} />
        </OverviewSection>
      </SettingsGroup>

      <SettingsGroup id="sync" title={titles.sync}>
        <OverviewSection
          title={t("syncTitle")}
          description={t("syncDescription")}
        >
          <OperationsPanel repoId={id} repoName={repo.name} />
        </OverviewSection>
        <RepoSetupRows repoId={id} repoName={repo.name} ids={SYNC_ROWS} />
      </SettingsGroup>

      <SettingsGroup id="coverage" title={titles.coverage}>
        <RepoSetupRows repoId={id} repoName={repo.name} ids={COVERAGE_ROWS} />
      </SettingsGroup>

      <SettingsGroup id="server" title={titles.server}>
        <OverviewSection
          title="Publish on repowise.dev"
          description="Put this repository on repowise.dev, indexed from its GitHub remote. Nothing on this machine is uploaded."
        >
          <PublishPanel repoId={id} />
        </OverviewSection>
      </SettingsGroup>

      {/* Not a tinted card. A destructive action earns emphasis on the verb and
          the sentence, not on a coloured ground the eye reads before either. */}
      <section className="flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:pt-8">
        <h2 className="text-base font-semibold tracking-tight text-[var(--color-error)]">
          {t("deleteTitle")}
        </h2>
        <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
          {t("deleteDescription")}
        </p>
        <div>
          <DeleteRepoButton
            repoId={id}
            repoName={repo.name}
            variant="button"
            redirectTo="/"
          />
        </div>
      </section>

      <p className="border-t border-[var(--color-border-default)] pt-6 text-xs text-[var(--color-text-tertiary)]">
        {t.rich("footer", {
          link: (chunks) => (
            <Link
              href="/settings"
              className="text-[var(--color-accent-primary)] hover:underline"
            >
              {chunks}
            </Link>
          ),
        })}
      </p>
    </PageShell>
  );
}

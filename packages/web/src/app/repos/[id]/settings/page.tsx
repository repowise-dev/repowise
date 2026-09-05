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

  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }

  const coordinatorHealth = await getCoordinatorHealth(id).catch(() => null);

  return (
    <PageShell
      title="Settings"
      description={`Indexing, model keys and health for ${repo.name}. Server, webhook and editor configuration are shared across repositories.`}
      className="max-w-3xl"
    >
      <OverviewSection
        title="General"
        description="Where this repository lives and which branch is indexed."
        flush
      >
        <RepoSettingsForm repo={repo} />
      </OverviewSection>

      <OverviewSection
        title="Sync and indexing"
        description="Pull recent commits into the existing index, or rebuild it from scratch."
      >
        <OperationsPanel repoId={id} repoName={repo.name} />
      </OverviewSection>

      <OverviewSection
        id="provider"
        title="AI provider"
        description="A model key lets this repository's documentation be written with AI. Keys are stored with the repository and used by generation from the UI or the CLI."
      >
        <ProviderSettingsPanel repoId={id} />
      </OverviewSection>

      <OverviewSection
        title="Decision capture"
        description="What mines architectural decisions from this repository, and whether any of it may send prose to your configured model. Capture only ever produces candidates: nothing here can accept a decision on your behalf."
      >
        <DecisionCaptureSettingsWrapper repoId={id} />
      </OverviewSection>

      <OverviewSection
        title="Refactoring code generation"
        description="Opt in to turning refactoring plans into reviewable diffs with your configured model."
      >
        <RefactoringSettingsSection repoId={id} />
      </OverviewSection>

      <OverviewSection
        title="Index health"
        description="Per-population drift: wiki pages against page vectors, and decision records against decision vectors. A gap means search is answering from a stale set."
      >
        <CoordinatorHealthPanel repoId={id} initial={coordinatorHealth} />
      </OverviewSection>

      {/* Not a tinted card. A destructive action earns emphasis on the verb and
          the sentence, not on a coloured ground the eye reads before either. */}
      <section className="flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:pt-8">
        <h2 className="text-base font-semibold tracking-tight text-[var(--color-error)]">
          Delete this repository
        </h2>
        <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
          Removes the index and everything generated from it — pages, symbols,
          decisions and history. Your source files are untouched. This cannot be
          undone.
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
        Server connection, webhooks, model defaults and MCP configuration are in{" "}
        <Link
          href="/settings"
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          global settings
        </Link>
        .
      </p>
    </PageShell>
  );
}

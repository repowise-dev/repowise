import type { Metadata } from "next";
import { Suspense } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { GitPullRequest } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { CommitFilesTable } from "@repowise-dev/ui/commits/commit-files-table";
import { formatDate } from "@repowise-dev/ui/lib/format";
import { changePromptFixes } from "@repowise-dev/ui/health/ai-prompt-builder";
import type { ChangeRecord } from "@repowise-dev/types/changes";
import { categoryLabel, changeLabel, changePromptInput, splitEvidence } from "@/lib/changes";
import { getChange } from "@/lib/api/changes";
import { ChangeHealthLede, ChangeGotWorse } from "@/components/changes/change-health";
import { ChangeChecks } from "@/components/changes/change-checks";
import { ChangeOutcomes } from "@/components/changes/change-outcomes";
import { ChangePromptAction } from "@/components/changes/change-prompt-action";

interface Props {
  params: Promise<{ id: string; changeId: string }>;
}

/** Above this many files, the list starts collapsed (as on the commit sheet). */
const LONG_FILE_LIST = 10;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id, changeId } = await params;
  try {
    const change = await getChange(id, decodeURIComponent(changeId));
    return { title: `${changeLabel(change)} — Change` };
  } catch {
    return { title: "Change" };
  }
}

/** The change's facts in one mono line; forge facts only when known. */
async function MetaLine({ change }: { change: ChangeRecord }) {
  const t = await getTranslations("views.changes");
  const meta = change.metadata;
  const prov = change.provenance;
  const pct = prov?.coverage == null ? null : Math.round(prov.coverage * 100);
  const parts = [
    changeLabel(change),
    categoryLabel(t, change.category),
    change.merged_at ? t("metaMerged", { date: formatDate(change.merged_at) }) : null,
    change.head_sha.slice(0, 12),
    `+${change.lines_added} −${change.lines_deleted}`,
    t("metaCommits", { count: change.commit_count }),
    t("metaFiles", { count: change.files_changed }),
    prov
      ? `${t("metaProvenance", { label: prov.label.replace("_", " ") })}${pct == null ? "" : ` (${t("coverage", { pct })})`}`
      : null,
    meta.approvals != null ? t("metaApprovals", { count: meta.approvals }) : null,
    meta.linked_issues?.length ? t("metaIssues", { issues: meta.linked_issues.join(", ") }) : null,
  ].filter((p): p is string => p !== null);
  return (
    <p className="font-mono text-xs text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
      {parts.join(" · ")}
    </p>
  );
}

/**
 * The evidence checklist and outcomes, streamed: the first read of a change
 * builds them. When the change made nothing worse, a review of the rows
 * needing a look is the page's one action.
 */
async function ChangeEvidence({ repoId, change }: { repoId: string; change: ChangeRecord }) {
  const t = await getTranslations("views.changes");
  let evidence = null;
  let building = false;
  try {
    const full = await getChange(repoId, change.id, { evidence: true });
    evidence = full.evidence ?? null;
    building = full.evidence_building ?? false;
  } catch {
    evidence = null;
  }
  if (!evidence) {
    return (
      <OverviewSection title={t("detailEvidence")}>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {t(building ? "evidenceBuilding" : "evidenceUnavailable")}
        </p>
      </OverviewSection>
    );
  }
  const view = splitEvidence(evidence.items);
  const input = changePromptInput(t, change, view.exceptions);
  const review =
    !changePromptFixes(input) && view.exceptions.length > 0 ? (
      <ChangePromptAction change={input} />
    ) : undefined;
  return (
    <>
      <ChangeChecks
        exceptions={view.exceptions}
        passed={view.passed}
        generatedAt={evidence.generated_at}
        action={review}
      />
      {view.outcomes && <ChangeOutcomes item={view.outcomes} repoId={repoId} />}
    </>
  );
}

/** One Change Record: what it did to code health, then its evidence, files and commits. */
export default async function ChangeDetailPage({ params }: Props) {
  const { id, changeId } = await params;
  const t = await getTranslations("views.changes");

  let change;
  try {
    change = await getChange(id, decodeURIComponent(changeId));
  } catch {
    notFound();
  }
  const prompt = changePromptInput(t, change);

  return (
    <PageShell icon={<GitPullRequest />} title={change.title}>
      <div className="-mt-2">
        <MetaLine change={change} />
      </div>

      {/* Health leads from the change's own rollup, so it paints with the page. */}
      <ChangeHealthLede
        health={change.health}
        action={changePromptFixes(prompt) ? <ChangePromptAction change={prompt} /> : undefined}
      />
      <ChangeGotWorse health={change.health} prompt={prompt} />

      <Suspense
        fallback={<p className="text-xs text-[var(--color-text-tertiary)]">{t("evidenceLoading")}</p>}
      >
        <ChangeEvidence repoId={id} change={change} />
      </Suspense>

      <OverviewSection
        title={t("detailFiles")}
        hint={t("metaFiles", { count: change.files_changed })}
        collapsible
        defaultOpen={change.files.length <= LONG_FILE_LIST}
      >
        <CommitFilesTable
          noun="change"
          files={change.files.map((f) => ({ path: f.path, lines_added: f.added, lines_deleted: f.deleted }))}
        />
      </OverviewSection>

      <OverviewSection
        title={t("detailCommits")}
        hint={t("metaCommits", { count: change.commits.length })}
        collapsible
        defaultOpen={false}
      >
        <ul className="space-y-1 font-mono text-xs">
          {change.commits.map((sha) => (
            <li key={sha}>
              <Link
                href={`/repos/${id}/commits?commit=${sha}`}
                className="rounded-sm text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
              >
                {sha}
              </Link>
            </li>
          ))}
        </ul>
      </OverviewSection>
    </PageShell>
  );
}

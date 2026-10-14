import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { GitPullRequest } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { formatDate } from "@repowise-dev/ui/lib/format";
import { changeLabel } from "@/components/changes/changes-table";
import { getChange } from "@/lib/api/changes";

interface Props {
  params: Promise<{ id: string; changeId: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id, changeId } = await params;
  try {
    const change = await getChange(id, decodeURIComponent(changeId));
    return { title: `${changeLabel(change)} — Change` };
  } catch {
    return { title: "Change" };
  }
}

/** One Change Record: what landed, from which commits, with what provenance. */
export default async function ChangeDetailPage({ params }: Props) {
  const { id, changeId } = await params;
  const t = await getTranslations("views.changes");

  let change;
  try {
    change = await getChange(id, decodeURIComponent(changeId));
  } catch {
    notFound();
  }

  const meta = change.metadata;
  const prov = change.provenance;
  const facts: [string, string][] = [
    [t("shape"), change.shape],
    [t("category"), change.category],
    [t("merged"), change.merged_at ? formatDate(change.merged_at) : t("unknown")],
    [t("head"), change.head_sha.slice(0, 12)],
    [t("lines"), `+${change.lines_added} -${change.lines_deleted}`],
  ];
  if (prov) {
    const pct = prov.coverage == null ? null : Math.round(prov.coverage * 100);
    facts.push([
      t("provenance"),
      `${prov.label.replace("_", " ")}${pct == null ? "" : ` (${t("coverage", { pct })})`}`,
    ]);
  }

  return (
    <PageShell
      icon={<GitPullRequest />}
      title={`${changeLabel(change)} ${change.title}`}
      actions={
        <Link href={`/repos/${id}/changes`} className="text-xs hover:underline">
          {t("back")}
        </Link>
      }
    >
      <dl className="mb-6 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-[var(--color-text-tertiary)]">{k}</dt>
            <dd className="font-mono text-xs text-[var(--color-text-primary)]">{v}</dd>
          </div>
        ))}
      </dl>

      <OverviewSection title={`${t("detailFiles")} (${change.files_changed})`}>
        <ul className="space-y-1 font-mono text-xs">
          {change.files.map((f) => (
            <li key={f.path} className="flex justify-between gap-4">
              <span className="truncate">{f.path}</span>
              <span className="shrink-0 tabular-nums">
                <span className="text-[var(--color-success)]">+{f.added}</span>{" "}
                <span className="text-[var(--color-error)]">-{f.deleted}</span>
              </span>
            </li>
          ))}
        </ul>
      </OverviewSection>

      <OverviewSection title={`${t("detailCommits")} (${change.commits.length})`}>
        <ul className="space-y-1 font-mono text-xs">
          {change.commits.map((sha) => (
            <li key={sha}>{sha}</li>
          ))}
        </ul>
      </OverviewSection>

      <OverviewSection title={t("detailMetadata")}>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
          <div>
            <dt className="text-[var(--color-text-tertiary)]">{t("source")}</dt>
            <dd>{meta.source ?? t("unknown")}</dd>
          </div>
          <div>
            <dt className="text-[var(--color-text-tertiary)]">{t("linkedIssues")}</dt>
            <dd>{meta.linked_issues?.join(", ") ?? t("unknown")}</dd>
          </div>
          <div>
            <dt className="text-[var(--color-text-tertiary)]">{t("approvals")}</dt>
            <dd>{meta.approvals ?? t("unknown")}</dd>
          </div>
        </dl>
      </OverviewSection>
    </PageShell>
  );
}

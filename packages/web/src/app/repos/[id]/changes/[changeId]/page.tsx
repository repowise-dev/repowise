import type { Metadata } from "next";
import { Suspense } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { GitPullRequest } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { formatDate } from "@repowise-dev/ui/lib/format";
import { Badge } from "@repowise-dev/ui/ui/badge";
import type { EvidenceItem } from "@repowise-dev/types/changes";
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

const STATUS_VARIANT = { ok: "fresh", attention: "stale", unknown: "outline" } as const;
const STATUS_KEY = { ok: "statusOk", attention: "statusAttention", unknown: "statusUnknown" } as const;

/** An item's value in one line; an unknown one shows why. */
function valueText(item: EvidenceItem): string {
  const value = item.value;
  if (value == null) {
    const reason = item.details.find((d) => typeof d.reason === "string")?.reason;
    return typeof reason === "string" ? reason : "";
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v != null && !(Array.isArray(v) && v.length === 0))
      .map(([k, v]) => `${k.replaceAll("_", " ")} ${Array.isArray(v) ? v.join(", ") : String(v)}`)
      .join(" · ");
  }
  return String(value);
}

/** The evidence checklist, streamed in: the first read of a change builds it. */
async function EvidenceChecklist({ repoId, changeId }: { repoId: string; changeId: string }) {
  const t = await getTranslations("views.changes");
  let evidence;
  let building = false;
  try {
    const change = await getChange(repoId, changeId, { evidence: true });
    evidence = change.evidence;
    building = change.evidence_building ?? false;
  } catch {
    evidence = null;
  }
  if (!evidence) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        {t(building ? "evidenceBuilding" : "evidenceUnavailable")}
      </p>
    );
  }
  return (
    <>
      <ul className="divide-y divide-[var(--color-border-default)] text-xs">
        {evidence.items.map((item) => (
          <li key={item.kind} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
            <span className="w-48 shrink-0 font-medium text-[var(--color-text-primary)]">
              {item.label}
            </span>
            <Badge variant={STATUS_VARIANT[item.status]}>{t(STATUS_KEY[item.status])}</Badge>
            <span className="text-[var(--color-text-tertiary)]">{item.basis}</span>
            <span className="min-w-0 flex-1 break-words text-[var(--color-text-secondary)]">
              {valueText(item)}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
        {t("evidenceGenerated", { date: formatDate(evidence.generated_at) })}
      </p>
    </>
  );
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

      <OverviewSection title={t("detailEvidence")}>
        <Suspense
          fallback={
            <p className="text-xs text-[var(--color-text-tertiary)]">{t("evidenceLoading")}</p>
          }
        >
          <EvidenceChecklist repoId={id} changeId={decodeURIComponent(changeId)} />
        </Suspense>
      </OverviewSection>

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

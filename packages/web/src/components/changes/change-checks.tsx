import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { formatDate } from "@repowise-dev/ui/lib/format";
import type { EvidenceItem } from "@repowise-dev/types/changes";
import { evidenceValueText, evidenceWord, type ChangesTranslator } from "@/lib/changes";

/** Attention is amber; unknown is a hollow neutral mark. A passed check has no mark. */
const DOT = {
  attention: "bg-[var(--color-warning)]",
  unknown: "border border-[var(--color-text-tertiary)]",
} as const;

const WORD = { attention: "statusAttention", unknown: "statusUnknown" } as const;

function CheckRow({ item, t }: { item: EvidenceItem; t: ChangesTranslator }) {
  const flagged = item.status === "ok" ? null : item.status;
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
      <span className="w-full shrink-0 text-[var(--color-text-primary)] sm:w-48">
        {evidenceWord(t, `evidenceKinds.${item.kind}`, item.label)}
      </span>
      {flagged && (
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-medium text-[var(--color-text-primary)]">
          <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[flagged]}`} />
          {t(WORD[flagged])}
        </span>
      )}
      <span className="min-w-[10rem] flex-1 break-words text-[var(--color-text-secondary)]">
        {evidenceValueText(t, item)}
      </span>
      {/* An unresolved basis repeats what the unknown mark already says. */}
      {item.basis !== "unresolved" && (
        <span className="text-[var(--color-text-tertiary)]">
          {evidenceWord(t, `evidenceBasis.${item.basis}`, item.basis)}
        </span>
      )}
    </li>
  );
}

const LIST = "divide-y divide-[var(--color-border-default)] text-xs";

/**
 * The evidence checklist, exceptions only: rows needing attention or not known
 * are listed with a dot and a word; the checks that passed fold into one
 * "N checks passed" line that opens on demand.
 */
export async function ChangeChecks({
  exceptions,
  passed,
  generatedAt,
  action,
}: {
  exceptions: EvidenceItem[];
  passed: EvidenceItem[];
  generatedAt: string;
  /** The page's primary action when it hands over a review of these rows. */
  action?: ReactNode;
}) {
  const t = await getTranslations("views.changes");
  const summary = exceptions.length
    ? t("checksPassed", { count: passed.length })
    : t("checksAllPassed", { count: passed.length });
  return (
    <OverviewSection title={t("detailEvidence")} action={action}>
      {exceptions.length > 0 && (
        <ul className={LIST}>
          {exceptions.map((item) => (
            <CheckRow key={item.kind} item={item} t={t} />
          ))}
        </ul>
      )}
      {passed.length > 0 && (
        <details className="group text-xs">
          <summary className="cursor-pointer text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
            {summary}
          </summary>
          <ul className={`${LIST} mt-1`}>
            {passed.map((item) => (
              <CheckRow key={item.kind} item={item} t={t} />
            ))}
          </ul>
        </details>
      )}
      <p className="text-xs text-[var(--color-text-tertiary)]">
        {t("evidenceGenerated", { date: formatDate(generatedAt) })}
      </p>
    </OverviewSection>
  );
}
